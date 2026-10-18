"""OpenAI-compatible API for the conversational agent.

Exposes the agent as an OpenAI-compatible provider for OpenWebUI to connect
to. Endpoints:
  - GET  /v1/models             : lists the available models (the agent).
  - POST /v1/chat/completions   : conversation with the agent.

For human-in-the-loop, the response may contain a `confirmation_required`
block (with `thread_id` and `interrupt_id`) indicating that the agent paused
waiting for the professor's approval. The client (OpenWebUI via a
tool/action, or a script) must resume with `Command(resume=...)`.

Streaming usage: content is emitted in SSE chunks (`text/event-stream`).
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents_backend.conversational.graph import conversational_graph
from agents_backend.conversational.state import ConversationalState
from langgraph.types import Command

router = APIRouter(prefix="/v1")


class ChatMessage(BaseModel):
    role: str
    content: str | list | None = None


class ResumePayload(BaseModel):
    """Payload to resume an interrupt (human-in-the-loop)."""

    approved: bool = Field(..., description="Did the professor approve the pending action?")
    feedback: str | None = Field(
        default=None, description="Optional comment from the professor"
    )


class ChatCompletionRequest(BaseModel):
    model: str = "exam-agent"
    messages: list[ChatMessage]
    stream: bool = False
    thread_id: str | None = Field(default=None, description="Persistent conversation ID")
    resume: ResumePayload | None = Field(
        default=None,
        description="To resume an interrupt (human-in-the-loop): {'approved': bool}",
    )


def _content_to_text(content: str | list | None) -> str:
    """Converts the content of an OpenAI message (string or list of parts)
    to plain text.

    OpenWebUI, when files are attached, sends the user message with
    `content` as a LIST of parts (e.g. `[{"type":"text","text":...},
    {"type":"file",...}]` or with `<file .../>` tags). This function extracts the
    text and preserves the file tags so the agent can see the
    `file_id` of the attached file.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                part_type = part.get("type", "")
                if part_type == "text":
                    parts.append(str(part.get("text", "")))
                elif part_type == "file":
                    # Keep the file tag with its id/url so the agent can
                    # invoke `add_material_file`.
                    file = part.get("file", {}) or {}
                    fid = file.get("file_id") or part.get("id") or ""
                    fname = file.get("filename") or part.get("name") or "file"
                    parts.append(f'<file id="{fid}" name="{fname}"/>')
                else:
                    # Any other part: try to extract text.
                    text = part.get("text") or part.get("content") or ""
                    if text:
                        parts.append(str(text))
        return "\n".join(p for p in parts if p)
    return str(content)


def _to_langgraph_messages(messages: list[ChatMessage]) -> list[dict]:
    """Converts messages from OpenAI format to LangGraph format."""
    out = []
    for m in messages:
        if m.role in ("system", "user", "assistant", "tool"):
            out.append({"role": m.role, "content": _content_to_text(m.content)})
    return out


@router.get("/models")
def list_models():
    """Lists the agent model (OpenAI-compatible format)."""
    return {
        "object": "list",
        "data": [
            {
                "id": "exam-agent",
                "object": "model",
                "created": 0,
                "owned_by": "tesis-usfq",
            }
        ],
    }


@router.post("/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    """Chats with the agent. Returns the response (streaming or JSON)."""
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # Resume an interrupt (human confirmation) or start a new turn.
    if req.resume is not None:
        try:
            result = conversational_graph.invoke(
                Command(resume=req.resume.model_dump()),
                config=config,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Error resuming: {str(e)[:200]}")
    else:
        messages = _to_langgraph_messages(req.messages)
        initial: ConversationalState = {
            "messages": messages,
            "subject_id": None,
            "subject_name": None,
            "pending_item": None,
            "pending_question_id": None,
        }
        try:
            result = conversational_graph.invoke(initial, config=config)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Agent error: {str(e)[:200]}")

    # Extract the text of the final response.
    final_messages = result.get("messages", [])
    content = ""
    if final_messages:
        last = final_messages[-1]
        content = getattr(last, "content", "") or ""

    # Detect whether an interrupt is pending (human confirmation).
    pending_interrupt = None
    if getattr(result, "interrupts", None):
        pending_interrupt = {
            "thread_id": thread_id,
            "interrupts": [
                {
                    "type": i.type if hasattr(i, "type") else "confirm_write",
                    "value": getattr(i, "value", {}),
                }
                for i in result.interrupts
            ],
        }

    payload = {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "thread_id": thread_id,
        "confirmation_required": pending_interrupt,
    }

    if req.stream:
        async def event_stream():
            # Role chunk (start of the response).
            yield (
                "data: " + json.dumps({
                    "id": payload["id"],
                    "object": "chat.completion.chunk",
                    "model": req.model,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                }, ensure_ascii=False) + "\n\n"
            )
            # Emit the content in chunks (OpenAI streaming compatible).
            if content:
                for i in range(0, len(content), 20):
                    chunk_text = content[i:i + 20]
                    yield (
                        "data: " + json.dumps({
                            "id": payload["id"],
                            "object": "chat.completion.chunk",
                            "model": req.model,
                            "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}],
                        }, ensure_ascii=False) + "\n\n"
                    )
            # Chunk final.
            yield (
                "data: " + json.dumps({
                    "id": payload["id"],
                    "object": "chat.completion.chunk",
                    "model": req.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }, ensure_ascii=False) + "\n\n"
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return payload
