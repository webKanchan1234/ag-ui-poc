import asyncio
import json
import logging
import os
import re
from uuid import uuid4
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("ag-ui-bff")

if load_dotenv:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

app = FastAPI(title="AG-UI BFF", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def agui_event(event_type: str, **fields) -> str:
    payload = {"type": event_type, **fields}
    return "data: " + json.dumps(payload, ensure_ascii=True) + "\n\n"


def chunk_text(text: str, chunk_size: int) -> list[str]:
    if not text:
        return []
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def extract_user_prompt(body: dict) -> str:
    messages = body.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue

        content = msg.get("content", "")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return " ".join(parts).strip()

    return ""


async def openai_answer(prompt: str) -> tuple[str, str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return "", "OPENAI_API_KEY is not set"

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant. "
                    "Answer clearly and concisely. "
                    "If you are unsure, say that you do not know."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(base_url + "/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
            if isinstance(answer, str):
                return answer.strip(), ""
    except Exception as ex:
        logger.exception("openai_request_failed")
        return "", str(ex)

    return "", "OpenAI returned empty content"


# Detect unresolved/refusal wording in AI output before deciding escalation.
def ai_cannot_answer(answer: str) -> bool:
    if not answer.strip():
        return True

    text = answer.lower()
    unsure_patterns = [
        r"\bi don't know\b",
        r"\bi do not know\b",
        r"\bi am not sure\b",
        r"\bi'm not sure\b",
        r"\bi cannot\b",
        r"\bi can't\b",
        r"\bunable to\b",
        r"\bno information\b",
        r"\bi do not have access\b",
        r"\bi don't have access\b",
        r"\bno access to\b",
        r"\baccess to external\b",
        r"\bexternal databases?\b",
        r"\bcase management systems?\b",
        r"\bcannot check\b",
        r"\bcan't check\b",
        r"\bcontact support\b",
        r"\bcheck the relevant website\b",
    ]
    return any(re.search(pattern, text) for pattern in unsure_patterns)


# Detect direct user intent to talk to a live/human support agent.
def requests_live_agent(prompt: str) -> bool:
    text = (prompt or "").lower().strip()
    direct_patterns = [
        r"\blive\s*agent\b",
        r"\bhuman\s*(agent|support)?\b",
        r"\brepresentative\b",
        r"\bescalate\b",
        r"\bconnect me\b.*\b(agent|human)\b",
        r"\btransfer\b.*\b(agent|human)\b",
    ]
    return any(re.search(pattern, text) for pattern in direct_patterns)


def local_agent_response(prompt: str) -> str:
    return (
        "I could not confidently answer that. "
        "I have redirected this to the local agent. "
        "Local agent response: Please share any extra details (error text, logs, or steps), and I will continue from there."
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "ag-ui-bff"}


@app.post("/ag-ui")
async def ag_ui(request: Request) -> StreamingResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    thread_id = body.get("threadId") or ("thread-" + str(uuid4()))
    run_id = body.get("runId") or ("run-" + str(uuid4()))
    message_id = "msg-" + str(uuid4())
    prompt = extract_user_prompt(body)

    if not prompt:
        raise HTTPException(status_code=400, detail="No user prompt found in messages")

    ai_text, ai_error = await openai_answer(prompt)

    route = "ai"
    final_answer = ai_text
    if requests_live_agent(prompt) or ai_cannot_answer(ai_text):
        route = "local-agent"
        final_answer = local_agent_response(prompt)

    async def stream():
        yield agui_event("RUN_STARTED", threadId=thread_id, runId=run_id)

        if route == "local-agent":
            yield agui_event(
                "CUSTOM",
                name="LOCAL_AGENT_REDIRECT",
                value={
                    "threadId": thread_id,
                    "runId": run_id,
                    "target": "local-agent",
                    "reason": "ai_unresolved",
                },
            )
            if ai_error:
                yield agui_event(
                    "CUSTOM",
                    name="AI_ERROR",
                    value={
                        "threadId": thread_id,
                        "runId": run_id,
                        "reason": ai_error,
                    },
                )

        yield agui_event(
            "TEXT_MESSAGE_START",
            threadId=thread_id,
            runId=run_id,
            messageId=message_id,
            role="assistant",
            name=route,
        )

        stream_chunk_size = int(os.getenv("AGUI_STREAM_CHUNK_SIZE", "24"))
        stream_chunk_delay = float(os.getenv("AGUI_STREAM_CHUNK_DELAY", "0.02"))

        for chunk in chunk_text(final_answer, stream_chunk_size):
            yield agui_event(
                "TEXT_MESSAGE_CONTENT",
                threadId=thread_id,
                runId=run_id,
                messageId=message_id,
                delta=chunk,
            )
            await asyncio.sleep(stream_chunk_delay)

        yield agui_event(
            "TEXT_MESSAGE_END",
            threadId=thread_id,
            runId=run_id,
            messageId=message_id,
        )

        yield agui_event(
            "RUN_FINISHED",
            threadId=thread_id,
            runId=run_id,
            outcome="completed",
            route=route,
        )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
