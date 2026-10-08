import asyncio
import ast
import json
import logging
import os
import re
import time
from uuid import UUID, uuid4
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from app.conversations import Message, connect_live ,find_conversation, prepare_turn
from app.conversations import router as conversations_router

from ag_ui.core import (
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
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
app.include_router(conversations_router)

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
    grant_type = os.getenv("HPESC_GRANT_TYPE", "client_credentials").strip()
    timeout_seconds = float(os.getenv("HPESC_TIMEOUT_SECONDS", "20"))

    if not token_url or not client_id or not client_secret:
        return "", "HPESC_TOKEN_URL, HPESC_CLIENT_ID or HPESC_CLIENT_SECRET is not set"

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": grant_type,
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


# Groups the upstream's own native TOOL_CALL_START/ARGS/RESULT SSE events by toolCallId into
# one {name, args, result} entry per call, regardless of whether the run also produced text -
# these are otherwise invisible plumbing our own stream never surfaced to the frontend.
def _collect_native_tool_calls(events: list[dict]) -> list[dict]:
    calls: dict[str, dict] = {}
    order: list[str] = []
    for event in events:
        call_id = event.get("toolCallId")
        if not call_id:
            continue
        etype = event.get("type")
        if etype == "TOOL_CALL_START":
            calls[call_id] = {"name": event.get("toolCallName", "tool"), "args_raw": "", "result": None}
            order.append(call_id)
        elif etype == "TOOL_CALL_ARGS" and call_id in calls:
            calls[call_id]["args_raw"] += event.get("delta", "")
        elif etype == "TOOL_CALL_RESULT" and call_id in calls:
            calls[call_id]["result"] = event.get("content")

    native_calls = []
    for call_id in order:
        call = calls[call_id]
        try:
            args = json.loads(call["args_raw"]) if call["args_raw"] else {}
        except json.JSONDecodeError:
            args = {"raw": call["args_raw"]}
        native_calls.append({"name": call["name"], "args": args, "result": call["result"]})
    return native_calls


async def hpesc_agent_answer(query: str, channel: str, context_id: str) -> tuple[str, str, list | None, list | None]:
    access_token, token_error = await get_hpesc_access_token()
    if not access_token:
        return "", token_error or "Unable to obtain access token", None, None

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
            logger.debug(
                "hpesc_agent_events thread_context=%s types=%s",
                context_id,
                [event.get("type") for event in events],
            )
            native_calls = _collect_native_tool_calls(events)

            deltas = [event.get("delta", "") for event in events if event.get("type") == "TEXT_MESSAGE_CONTENT"]
            answer = "".join(deltas).strip()
            if answer:
                return answer, "", None, native_calls

            # Some upstream runs skip TEXT_MESSAGE_CONTENT and only carry the answer in
            # RUN_FINISHED.result.output — check that before ever falling back to raw SSE text.
            for event in events:
                if event.get("type") == "RUN_FINISHED":
                    output = (event.get("result") or {}).get("output", "")
                    if output:
                        return str(output).strip(), "", None, native_calls

            # The upstream sometimes only invokes its own internal tool (e.g. a knowledge-base
            # search) and never follows up with any text. Its content is a string, not real JSON
            # (observed as Python repr, e.g. "[{'type': ...}]"), so try json first, then a safe
            # literal_eval, before giving up and falling back to the raw joined string.
            tool_results_raw = [
                event.get("content", "")
                for event in events
                if event.get("type") == "TOOL_CALL_RESULT" and event.get("content")
            ]
            if tool_results_raw:
                parsed_items = None
                try:
                    parsed = json.loads(tool_results_raw[0])
                except (json.JSONDecodeError, TypeError):
                    try:
                        parsed = ast.literal_eval(tool_results_raw[0])
                    except (ValueError, SyntaxError):
                        parsed = None
                if isinstance(parsed, list) and parsed and all(isinstance(i, dict) for i in parsed):
                    parsed_items = parsed

                if parsed_items is not None:
                    return "", "", parsed_items, native_calls

                joined = "\n\n".join(str(c).strip() for c in tool_results_raw)
                return joined, "", None, native_calls

            return "", "HPE Support Center agent returned empty content", None, native_calls
    except Exception as ex:
        logger.exception("hpesc_agent_request_failed")
        return "", str(ex), None, None


# The frontend renders whatever widget the agent asks for, as long as it matches one of
# these validated shapes (see safe_ui_spec below) - the agent never gets to emit raw markup.
DEFAULT_HANDOFF_UI = {
    "type": "button-group",
    "buttons": [
        {"label": "Yes, connect me", "value": {"approved": True}, "variant": "primary"},
        {"label": "No, continue with AI", "value": {"approved": False}},
    ],
}


# Shared shape check for widgets keyed by a list of {label, value} items (buttons/options differ only in key name).
def _validate_labeled_list(ui: dict, key: str) -> bool:
    items = ui.get(key)
    return isinstance(items, list) and len(items) > 0 and all(
        isinstance(item, dict) and isinstance(item.get("label"), str) and "value" in item for item in items
    )


def _validate_button_group(ui: dict) -> bool:
    return _validate_labeled_list(ui, "buttons")


def _validate_select(ui: dict) -> bool:
    return _validate_labeled_list(ui, "options")


def _validate_card(ui: dict) -> bool:
    return isinstance(ui.get("title"), str) and bool(ui.get("title"))


def _validate_carousel(ui: dict) -> bool:
    items = ui.get("items")
    return isinstance(items, list) and len(items) > 0 and all(
        isinstance(item, dict) and _validate_card(item) for item in items
    )


# The small, fixed vocabulary of widgets the frontend knows how to render; the LLM may only
# pick from these shapes, never emit arbitrary markup.
UI_WIDGET_VALIDATORS = {
    "button-group": _validate_button_group,
    "select": _validate_select,
    "card": _validate_card,
    "carousel": _validate_carousel,
}


# Only trust the model's own "ui" spec if it matches a known, validated widget shape.
def safe_ui_spec(args: dict, fallback: dict | None) -> dict | None:
    ui = args.get("ui")
    if isinstance(ui, dict):
        validator = UI_WIDGET_VALIDATORS.get(ui.get("type"))
        if validator and validator(ui):
            return ui
    return fallback


# The upstream's own native tool results (e.g. a knowledge-base search) have an unknown,
# arbitrary dict shape - derive a best-effort card title from common field names and dump the
# rest as the description, rather than assuming a fixed schema that may not match.
def _card_from_native_item(item: dict, index: int) -> dict:
    title = next(
        (item[key].strip() for key in ("title", "name", "subject", "label") if isinstance(item.get(key), str) and item[key].strip()),
        f"Result {index + 1}",
    )
    rest = {k: v for k, v in item.items() if k not in ("title", "name", "subject", "label")}
    card: dict = {"type": "card", "title": title}
    if rest:
        card["description"] = json.dumps(rest, default=str)[:500]
    return card


# Builds a carousel ui spec out of the upstream's own native tool-call result items (not our
# own ```tool fenced-block convention) so they're visible as a widget instead of raw dumped text.
def native_items_to_ui(items: list[dict]) -> dict:
    return {"type": "carousel", "items": [_card_from_native_item(item, i) for i in items]}


# Direct OpenAI access is blocked on this network, so tool decisions go through the same
# reachable HPE upstream agent used for plain answers: it's asked to optionally reply with a
# fenced ```tool block instead of native function-calling (which this upstream doesn't support
# for arbitrary tool schemas - it only ever invokes its own internal tools).
_TOOL_BLOCK_RE = re.compile(r"```tool\s*(\{.*?\})\s*```", re.DOTALL)

_TOOL_INSTRUCTIONS = (
    "You may optionally show an interactive widget instead of a plain text answer by replying "
    "with ONLY a single fenced block, exactly like:\n"
    "```tool\n"
    '{"name": "request_handoff", "args": {"reason": "...", "ui": {"type": "button-group", '
    '"buttons": [{"label": "Yes, connect me", "value": {"approved": true}, "variant": "primary"}, '
    '{"label": "No, continue with AI", "value": {"approved": false}}]}}\n'
    "```\n"
    "- only when the user explicitly asks to speak with a human, live agent, or representative - or:\n"
    "```tool\n"
    '{"name": "present_ui", "args": {"message": "...", "ui": {"type": "select", "options": '
    '[{"label": "...", "value": {...}}]}}}\n'
    "```\n"
    "(ui.type can also be 'card' with a title/description/actions, or 'carousel' with a list of "
    "such cards as 'items') - only when a widget clearly helps more than text, and only using "
    "options/titles/content you determine from the conversation. "
    "Otherwise just answer normally, with no fenced block."
)


# Ask the one reachable model (the HPE upstream agent) to either answer plainly or emit a tool
# call; returns (tool_call, answer_text, error) where tool_call is None for a plain answer.
async def decide_and_answer(prompt: str, channel: str, context_id: str) -> tuple[dict | None, str, str, list | None]:
    augmented_query = f"{_TOOL_INSTRUCTIONS}\n\nUser: {prompt}"
    text, error, native_items, native_calls = await hpesc_agent_answer(augmented_query, channel, context_id)
    if error:
        return None, text, error, native_calls

    # The upstream invoked its own internal tool (e.g. a knowledge-base search) and returned
    # structured results with no text of its own - surface them as a present_ui carousel instead
    # of falling through to the (empty) fenced-block search below.
    if native_items:
        args = {"message": "Here's what I found:", "ui": native_items_to_ui(native_items)}
        return {"name": "present_ui", "args": args}, "", "", native_calls

    if not text.strip():
        return None, text, error, native_calls

    match = _TOOL_BLOCK_RE.search(text)
    if not match:
        return None, text, "", native_calls

    try:
        call = json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("tool_block_parse_failed")
        return None, text, "", native_calls

    name = call.get("name")
    if name not in ("request_handoff", "present_ui"):
        return None, text, "", native_calls

    return {"name": name, "args": call.get("args") or {}}, "", "", native_calls


# Detect the frontend's reply to a pending tool call (e.g. a handoff Yes/No button click).
def extract_tool_result(body: dict) -> dict | None:
    messages = body.get("messages", [])
    if not messages:
        return None

    last = messages[-1]
    if last.get("role") != "tool":
        return None

    tool_call_id = last.get("toolCallId") or last.get("tool_call_id") or ""
    if not tool_call_id:
        return None

    return {"tool_call_id": str(tool_call_id), "content": str(last.get("content", ""))}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "ag-ui-bff"}


@app.post("/ag-ui")
async def ag_ui(request: Request) -> StreamingResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    run_id = body.get("runId") or ("run-" + str(uuid4()))
    message_id = "msg-" + str(uuid4())
    prompt = extract_user_prompt(body)
    channel = (body.get("channel") or "hpesc_agent").strip()
    tool_result = extract_tool_result(body)

    if not prompt and tool_result is None:
        raise HTTPException(status_code=400, detail="No user prompt found in messages")

    # A stored conversation is optional (older/other clients may not create one first),
    # but if a threadId is supplied it must resolve to a real, open conversation.
    # A tool-result continuation (button click) reuses existing history instead of
    # appending the prompt again via prepare_turn.
    conversation = None
    thread_id_raw = body.get("threadId")
    if thread_id_raw:
        if tool_result is not None:
            conversation = find_conversation(UUID(str(thread_id_raw)))
        else:
            conversation, _ = prepare_turn(thread_id_raw, prompt)
        thread_id = str(conversation.threadId)
    else:
        thread_id = "thread-" + str(uuid4())

    # AG-UI's HttpAgent serializes custom per-run data under "forwardedProps", not as a
    # top-level field, so that's the frontend-supplied source (random, per browser session).
    forwarded_props = body.get("forwardedProps") or {}
    context_id = (forwarded_props.get("context_id") or body.get("context_id") or thread_id).strip()
    encoder = EventEncoder()

    def send(event) -> str:
        return encoder.encode(event)

    def run_finished(route: str) -> str:
        return send(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id, route=route))

    async def stream():
        # Emit RUN_STARTED before the slow upstream call so the frontend can show its
        # loading indicator immediately instead of waiting for the full answer.
        yield send(RunStartedEvent(type=EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id))

        stream_chunk_size = int(os.getenv("AGUI_STREAM_CHUNK_SIZE", "24"))
        stream_chunk_delay = float(os.getenv("AGUI_STREAM_CHUNK_DELAY", "0.02"))

        def persist_assistant(text: str) -> None:
            if conversation is not None:
                conversation.messages.append(Message(role="assistant", content=text))

        async def emit_tool_call(tool_call_id: str, name: str, args: dict):
            yield send(
                ToolCallStartEvent(
                    type=EventType.TOOL_CALL_START,
                    tool_call_id=tool_call_id,
                    tool_call_name=name,
                    parent_message_id=message_id,
                )
            )
            yield send(
                ToolCallArgsEvent(
                    type=EventType.TOOL_CALL_ARGS,
                    tool_call_id=tool_call_id,
                    delta=json.dumps(args),
                )
            )
            yield send(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tool_call_id))

        async def emit_text(text: str, name: str):
            yield send(
                TextMessageStartEvent(
                    type=EventType.TEXT_MESSAGE_START,
                    message_id=message_id,
                    role="assistant",
                    name=name,
                )
            )
            for chunk in chunk_text(text, stream_chunk_size):
                yield send(
                    TextMessageContentEvent(
                        type=EventType.TEXT_MESSAGE_CONTENT,
                        message_id=message_id,
                        delta=chunk,
                    )
                )
                await asyncio.sleep(stream_chunk_delay)
            yield send(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=message_id))

        # The user responded to a pending tool widget (handoff Yes/No, or a present_ui click);
        # resolve it directly instead of asking the LLM again.
        if (
            tool_result is not None
            and conversation is not None
            and conversation.interruptId is not None
            and str(conversation.interruptId) == tool_result["tool_call_id"]
        ):
            try:
                tool_payload = json.loads(tool_result["content"])
            except json.JSONDecodeError:
                tool_payload = {}

            pending_tool = conversation.pendingToolName
            conversation.interruptId = None
            conversation.pendingToolName = None

            if pending_tool == "request_handoff":
                approved = bool(tool_payload.get("approved"))
                if not approved:
                    conversation.status = "AI_ACTIVE"
                    final_answer = "Okay, staying with me. How else can I help?"
                else:
                    conversation.status = "LIVE_CONNECTING"
                    try:
                        result = await connect_live(conversation)
                        conversation.message = result["message"]
                        conversation.status = "LIVE_SIMULATED"
                    finally:
                        if conversation.status == "LIVE_CONNECTING":
                            conversation.status = "LIVE_UNCERTAIN"
                    final_answer = conversation.message
            else:
                # present_ui widgets have no backend side effects; just acknowledge the pick.
                final_answer = f"Got it, thanks \u2014 you picked: {json.dumps(tool_payload)}"

            persist_assistant(final_answer)
            async for event in emit_text(final_answer, "handoff"):
                yield event
            yield run_finished("handoff")
            return

        ai_text = ""
        ai_error = ""
        route = "ai"
        tool_call = None
        native_calls = None

        if conversation is not None and conversation.status != "AI_ACTIVE":
            route = "handoff"
            final_answer = (
                conversation.message
                or "This conversation is not accepting AI messages."
            )
        else:
            tool_call, ai_text, ai_error, native_calls = await decide_and_answer(prompt, channel, context_id)
            if tool_call:
                route = "tool"
            else:
                final_answer = ai_text

        if route == "ai" and (ai_error or not ai_text.strip()):
            final_answer = "Unable to get a response from HPE Support Center. Please try again."
            logger.warning("ag_ui_upstream_failed thread_id=%s run_id=%s", thread_id, run_id)
            persist_assistant(final_answer)
            yield send(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=final_answer,
                    code="UPSTREAM_ERROR",
                )
            )
            return

        if route == "tool":
            # A fresh id (not any upstream-internal id), so it matches the conversation's
            # interruptId when the frontend sends the result back.
            tool_call_id = str(uuid4())
            name = tool_call["name"]
            args = tool_call["args"]

            if name == "request_handoff":
                final_answer = "Would you like me to connect you with a live agent?"
                ui_spec = safe_ui_spec(args, DEFAULT_HANDOFF_UI)
            else:
                final_answer = args.get("message", "Here's something that might help:")
                ui_spec = safe_ui_spec(args, None)

            if conversation is not None:
                if name == "request_handoff":
                    conversation.status = "HANDOFF_PENDING"
                    conversation.message = final_answer
                if ui_spec is not None:
                    conversation.interruptId = UUID(tool_call_id)
                    conversation.pendingToolName = name
            persist_assistant(final_answer)

            async for event in emit_text(final_answer, route):
                yield event

            if ui_spec is not None:
                async for event in emit_tool_call(tool_call_id, name, {**args, "ui": ui_spec}):
                    yield event

            yield run_finished(route)
            return

        # Relay the upstream's own native tool invocation(s) (e.g. a knowledge-base search that
        # ALSO produced a direct text answer) as real ToolCallStart/Args/End events, so they're
        # visible in our own stream instead of being silently swallowed - matches what the raw
        # upstream SSE already shows, even though the frontend only renders a widget if a later
        # tool call also supplies a "ui" (these carry none, so they're relay-only/no-op visually).
        if route == "ai" and native_calls:
            for call in native_calls:
                relay_id = str(uuid4())
                async for event in emit_tool_call(relay_id, call["name"], call.get("args") or {}):
                    yield event

        # Persist the assistant's reply now that we know it's actually going out to the client.
        persist_assistant(final_answer)

        async for event in emit_text(final_answer, route):
            yield event

        yield run_finished(route)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )