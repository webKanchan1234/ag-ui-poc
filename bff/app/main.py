import asyncio
import json
import logging
import os
import re
import time
from uuid import uuid4
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ag_ui.core import (
    CustomEvent,
    EventType,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("ag-ui-bff")

if load_dotenv:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

# Corporate VPN/proxy re-signs these internal HPE domains with a root CA that isn't in
# Python's trust store (mirrors the Node httpsAgent rejectUnauthorized:false workaround).
# Prefer setting SSL_CERT_FILE/REQUESTS_CA_BUNDLE to the corp CA bundle over this for anything beyond local dev.
HPESC_TLS_VERIFY = os.getenv("HPESC_VERIFY_SSL", "false").strip().lower() in ("1", "true", "yes")

app = FastAPI(title="AG-UI BFF", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# Cached in-memory so we don't request a new OAuth token on every chat turn.
_hpesc_token_cache = {"access_token": "", "expires_at": 0.0}


async def get_hpesc_access_token() -> tuple[str, str]:
    now = time.time()
    if _hpesc_token_cache["access_token"] and now < _hpesc_token_cache["expires_at"]:
        return _hpesc_token_cache["access_token"], ""

    token_url = os.getenv("HPESC_TOKEN_URL", "").strip()
    client_id = os.getenv("HPESC_CLIENT_ID", "").strip()
    client_secret = os.getenv("HPESC_CLIENT_SECRET", "").strip()
    timeout_seconds = float(os.getenv("HPESC_TIMEOUT_SECONDS", "20"))

    if not token_url or not client_id or not client_secret:
        return "", "HPESC_TOKEN_URL, HPESC_CLIENT_ID or HPESC_CLIENT_SECRET is not set"

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, verify=HPESC_TLS_VERIFY) as client:
            response = await client.post(token_url, data=data)
            response.raise_for_status()
            payload = response.json()
            access_token = payload.get("access_token", "")
            if not access_token:
                return "", "Token response did not include access_token"

            expires_in = float(payload.get("expires_in", 300))
            _hpesc_token_cache["access_token"] = access_token
            _hpesc_token_cache["expires_at"] = now + max(expires_in - 30, 30)
            return access_token, ""
    except Exception as ex:
        logger.exception("hpesc_token_request_failed")
        return "", str(ex)


# Splits a raw text/event-stream body into individual JSON event objects ("data: {...}" blocks).
def parse_sse_events(raw: str) -> list[dict]:
    events = []
    for chunk in raw.split("\n\n"):
        data_line = next((line for line in chunk.split("\n") if line.startswith("data:")), None)
        if not data_line:
            continue

        data_str = data_line[len("data:") :].strip()
        if not data_str:
            continue

        try:
            events.append(json.loads(data_str))
        except json.JSONDecodeError:
            continue

    return events


async def hpesc_agent_answer(query: str, channel: str, context_id: str) -> tuple[str, str]:
    access_token, token_error = await get_hpesc_access_token()
    if not access_token:
        return "", token_error or "Unable to obtain access token"

    agent_url = os.getenv(
        "HPESC_AGENT_URL",
        "https://api-gw-ext-dev.support.hpe.com/apigwext/llmaas/hpescagent/v1/graph-stream",
    ).strip()
    timeout_seconds = float(os.getenv("HPESC_TIMEOUT_SECONDS", "20"))

    payload = {
        "query": query,
        "channel": channel,
        "context_id": context_id,
    }
    headers = {
        "Authorization": "Bearer " + access_token,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, verify=HPESC_TLS_VERIFY) as client:
            response = await client.post(agent_url, headers=headers, json=payload)
            response.raise_for_status()

            # The upstream agent replies with its own AG-UI SSE event stream; pull the
            # assistant text out of the TEXT_MESSAGE_CONTENT deltas rather than treating
            # the body as a single JSON payload.
            events = parse_sse_events(response.text)

            deltas = [event.get("delta", "") for event in events if event.get("type") == "TEXT_MESSAGE_CONTENT"]
            answer = "".join(deltas).strip()
            if answer:
                return answer, ""

            # Some upstream runs skip TEXT_MESSAGE_CONTENT and only carry the answer in
            # RUN_FINISHED.result.output — check that before ever falling back to raw SSE text.
            for event in events:
                if event.get("type") == "RUN_FINISHED":
                    output = (event.get("result") or {}).get("output", "")
                    if output:
                        return str(output).strip(), ""

            return "", "HPE Support Center agent returned empty content"
    except Exception as ex:
        logger.exception("hpesc_agent_request_failed")
        return "", str(ex)


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
    channel = (body.get("channel") or "hpesc_agent").strip()
    context_id = (body.get("context_id") or thread_id).strip()

    if not prompt:
        raise HTTPException(status_code=400, detail="No user prompt found in messages")

    encoder = EventEncoder()

    def send(event) -> str:
        return encoder.encode(event)

    async def stream():
        # Emit RUN_STARTED before the slow upstream call so the frontend can show its
        # loading indicator immediately instead of waiting for the full answer.
        yield send(RunStartedEvent(type=EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id))

        ai_text, ai_error = await hpesc_agent_answer(prompt, channel, context_id)

        route = "ai"
        final_answer = ai_text
        if requests_live_agent(prompt):
            route = "local-agent"
            final_answer = local_agent_response(prompt)

        if route == "local-agent":
            yield send(
                CustomEvent(
                    type=EventType.CUSTOM,
                    name="LOCAL_AGENT_REDIRECT",
                    value={
                        "threadId": thread_id,
                        "runId": run_id,
                        "target": "local-agent",
                        "reason": "ai_unresolved",
                    },
                )
            )
            if ai_error:
                yield send(
                    CustomEvent(
                        type=EventType.CUSTOM,
                        name="AI_ERROR",
                        value={
                            "threadId": thread_id,
                            "runId": run_id,
                            "reason": ai_error,
                        },
                    )
                )

        yield send(
            TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START,
                message_id=message_id,
                role="assistant",
                name=route,
            )
        )

        stream_chunk_size = int(os.getenv("AGUI_STREAM_CHUNK_SIZE", "24"))
        stream_chunk_delay = float(os.getenv("AGUI_STREAM_CHUNK_DELAY", "0.02"))

        for chunk in chunk_text(final_answer, stream_chunk_size):
            yield send(
                TextMessageContentEvent(
                    type=EventType.TEXT_MESSAGE_CONTENT,
                    message_id=message_id,
                    delta=chunk,
                )
            )
            await asyncio.sleep(stream_chunk_delay)

        yield send(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=message_id))

        yield send(
            RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                thread_id=thread_id,
                run_id=run_id,
                route=route,
            )
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
