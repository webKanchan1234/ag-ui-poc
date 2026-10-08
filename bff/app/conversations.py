import json
from typing import Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, StrictBool
from langchain_openai import ChatOpenAI
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


# Group all conversation endpoints under /conversations.
router = APIRouter(prefix="/conversations", tags=["Conversations"])

model_name = os.getenv("HPESC_MODEL", "").strip()
api_key = os.getenv("OPENAI_API_KEY", "").strip()
timeout_seconds = float(os.getenv("HPESC_TIMEOUT_SECONDS", "20"))


if not model_name:
    raise RuntimeError("HPESC_MODEL is not configured")
if not api_key:
    raise RuntimeError("OPENAI_API_KEY is not configured")

model = ChatOpenAI(
    model=model_name,
    api_key=api_key,
    timeout=timeout_seconds,
    max_retries=0,
)

# Store one chat message with its ID, sender, and text.
class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    role: Literal["user", "assistant"]
    content: str


# Store a conversation's status, messages, and user details.
class Conversation(BaseModel):
    threadId: UUID = Field(default_factory=uuid4)
    status: Literal[
        "AI_ACTIVE", "HANDOFF_PENDING", "LIVE_CONNECTING",
        "LIVE_SIMULATED", "LIVE_UNCERTAIN", "CLOSED",
    ] = "AI_ACTIVE"
    interruptId: UUID | None = None
    pendingToolName: str | None = None
    message: str = ""
    messages: list[Message] = Field(default_factory=list)
    user_context: dict[str, str] = Field(default_factory=dict)

# Temporary storage: all conversations disappear when the server restarts.
conversations: dict[UUID, Conversation] = {}

model = os.getenv("HPESC_MODEL", "").strip()
timeout_seconds = float(os.getenv("HPESC_TIMEOUT_SECONDS", "20"))
model = ChatOpenAI(model=model, timeout=timeout_seconds, max_retries=0)



class Decision(BaseModel):
    action: Literal["continue", "request_handoff"]
    reply: str

class ChatInput(BaseModel):
    threadId: UUID
    message: str = Field(min_length=1, max_length=4000)

class Confirmation(BaseModel):
    model_config = {"extra": "forbid"}
    interruptId: UUID
    approved: StrictBool

async def connect_live(conversation):
    return {"status": "LIVE_SIMULATED", "message": "Hi, I am a live agent."}


# Find a conversation by ID, or return a 404 error if it does not exist.
def find_conversation(thread_id: UUID) -> Conversation:
    if thread_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversations[thread_id]


# Save a user message and build the history/context to send to the AI.
# The chat endpoint must call this function; it does not run automatically.
def prepare_turn(thread_id: str, prompt: str) -> tuple[Conversation, str]:
    try:
        conversation = find_conversation(UUID(str(thread_id)))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid threadId")
    if conversation.status == "CLOSED":
        raise HTTPException(status_code=409, detail="Conversation is closed")
    conversation.messages.append(Message(role="user", content=prompt))
    query = json.dumps({
        "user_context": conversation.user_context,
        "messages": [{"role": item.role, "content": item.content} for item in conversation.messages],
    })
    return conversation, query


# Create and store an empty conversation; return an independent copy.
@router.post("", response_model=Conversation, status_code=201)
async def create_conversation(response: Response) -> Conversation:
    conversation = Conversation()
    conversations[conversation.threadId] = conversation
    response.headers["Cache-Control"] = "no-store"
    return conversation.model_copy(deep=True)


# List all saved conversations, most recently created first, for a sidebar/history view.
@router.get("", response_model=list[Conversation])
async def list_conversations(response: Response) -> list[Conversation]:
    response.headers["Cache-Control"] = "no-store"
    return [conversation.model_copy(deep=True) for conversation in reversed(conversations.values())]


# Return a saved conversation, including its messages and user context.
@router.get("/{thread_id}", response_model=Conversation)
async def get_conversation(thread_id: UUID, response: Response) -> Conversation:
    response.headers["Cache-Control"] = "no-store"
    return find_conversation(thread_id).model_copy(deep=True)


# Replace user context, such as product and operating system, unless closed.
@router.patch("/{thread_id}/context", response_model=Conversation)
async def update_context(thread_id: UUID, context: dict[str, str], response: Response) -> Conversation:
    conversation = find_conversation(thread_id)
    if conversation.status == "CLOSED":
        raise HTTPException(status_code=409, detail="Conversation is closed")
    conversation.user_context = context.copy()
    response.headers["Cache-Control"] = "no-store"
    return conversation.model_copy(deep=True)

# Mark the conversation closed without deleting its history or context.
@router.post("/{thread_id}/close", response_model=Conversation)
async def close_conversation(thread_id: UUID, response: Response) -> Conversation:
    conversation = find_conversation(thread_id)
    conversation.status = "CLOSED"
    response.headers["Cache-Control"] = "no-store"
    return conversation.model_copy(deep=True)

@router.post("/{thread_id}/delete", response_model=Conversation)
async def delete_conversation(thread_id: UUID, response: Response) -> Conversation:
    conversation = find_conversation(thread_id)
    del conversations[thread_id]
    response.headers["Cache-Control"] = "no-store"
    return conversation.model_copy(deep=True)


@router.post("/chat", response_model=Conversation)
async def chat(payload: ChatInput):
    conversation = find_conversation(payload.threadId)
    if conversation.status != "AI_ACTIVE":
        raise HTTPException(409, "Conversation is not accepting AI messages")

    conversation, query = prepare_turn(str(payload.threadId), payload.message)
    decision = await model.with_structured_output(Decision).ainvoke([
        ("system", "Choose request_handoff only for an explicit request to speak "
         "with a person. Otherwise choose continue and answer normally. "
         "Never claim a representative is connected. "
         "The JSON input contains user_context and message history; "
         "respond to the last user message."),
        ("human", query),
    ])
    pending = decision.action == "request_handoff"
    conversation.status = "HANDOFF_PENDING" if pending else "AI_ACTIVE"
    conversation.interruptId = uuid4() if pending else None
    conversation.message = (
        "Connect to live agent?" if pending else decision.reply
    )
    conversation.messages.append(
        Message(role="assistant", content=conversation.message)
    )
    return conversation.model_copy(deep=True)


@router.post("/{thread_id}/handoff", response_model=Conversation)
async def confirm(thread_id: UUID, payload: Confirmation):
    conversation = find_conversation(thread_id)
    if (conversation.status != "HANDOFF_PENDING"
            or conversation.interruptId != payload.interruptId):
        raise HTTPException(409, "Stale or already handled confirmation")

    conversation.interruptId = None
    if not payload.approved:
        conversation.status = "AI_ACTIVE"
        conversation.message = "Transfer declined."
    else:
        conversation.status = "LIVE_CONNECTING"
        try:
            result = await connect_live(conversation)
            conversation.message = result["message"]
            conversation.status = "LIVE_SIMULATED"
        finally:
            if conversation.status == "LIVE_CONNECTING":
                conversation.status = "LIVE_UNCERTAIN"

    conversation.messages.append(
        Message(role="assistant", content=conversation.message)
    )
    return conversation.model_copy(deep=True)