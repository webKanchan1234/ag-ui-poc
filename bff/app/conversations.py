import json
from typing import Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

# Group all conversation endpoints under /conversations.
router = APIRouter(prefix="/conversations", tags=["Conversations"])


# Store one chat message with its ID, sender, and text.
class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    role: Literal["user", "assistant"]
    content: str


# Store a conversation's status, messages, and user details.
class Conversation(BaseModel):
    threadId: UUID = Field(default_factory=uuid4)
    status: Literal["AI_ACTIVE", "CLOSED"] = "AI_ACTIVE"
    messages: list[Message] = Field(default_factory=list)
    user_context: dict[str, str] = Field(default_factory=dict)

# Temporary storage: all conversations disappear when the server restarts.
conversations: dict[UUID, Conversation] = {}


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