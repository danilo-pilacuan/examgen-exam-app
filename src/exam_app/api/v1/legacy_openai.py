"""API OpenAI-compatible para el agente conversacional.

Expone el agente como un proveedor OpenAI-compatible para que OpenWebUI se
conecte. Endpoints:
  - GET  /v1/models             : lista los modelos disponibles (el agente).
  - POST /v1/chat/completions   : conversación con el agente.

Para el human-in-the-loop, la respuesta puede contener un bloque
`confirmation_required` (con `thread_id` y `interrupt_id`) indicando que el
agente pausó esperando la aprobación del profesor. El cliente (OpenWebUI vía
una tool/acción, o un script) debe reanudar con `Command(resume=...)`.

Uso de streaming: se emite el contenido en chunks SSE (`text/event-stream`).
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
    """Payload para reanudar un interrupt (human-in-the-loop)."""

    approved: bool = Field(..., description="¿El profesor aprobó la acción pendiente?")
    feedback: str | None = Field(
        default=None, description="Comentario opcional del profesor"
    )


class ChatCompletionRequest(BaseModel):
    model: str = "exam-agent"
    messages: list[ChatMessage]
    stream: bool = False
    thread_id: str | None = Field(default=None, description="ID de conversación persistente")
    resume: ResumePayload | None = Field(
        default=None,
        description="Para reanudar un interrupt (human-in-the-loop): {'approved': bool}",
    )


def _content_to_text(content: str | list | None) -> str:
    """Convierte el contenido de un mensaje OpenAI (string o lista de partes)
    a texto plano.

    OpenWebUI, cuando hay archivos adjuntos, envía el mensaje del usuario con
    `content` como una LISTA de partes (p. ej. `[{"type":"text","text":...},
    {"type":"file",...}]` o con tags `<file .../>`). Esta función extrae el
    texto y conserva los tags de archivo para que el agente pueda ver el
    `file_id` del archivo adjunto.
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
                    # Conservar el tag de archivo con su id/url para que el
                    # agente pueda invocar `agregar_material_archivo`.
                    file = part.get("file", {}) or {}
                    fid = file.get("file_id") or part.get("id") or ""
                    fname = file.get("filename") or part.get("name") or "archivo"
                    parts.append(f'<file id="{fid}" name="{fname}"/>')
                else:
                    # Cualquier otra parte: intentar extraer texto.
                    text = part.get("text") or part.get("content") or ""
                    if text:
                        parts.append(str(text))
        return "\n".join(p for p in parts if p)
    return str(content)


def _to_langgraph_messages(messages: list[ChatMessage]) -> list[dict]:
    """Convierte mensajes del formato OpenAI al formato de LangGraph."""
    out = []
    for m in messages:
        if m.role in ("system", "user", "assistant", "tool"):
            out.append({"role": m.role, "content": _content_to_text(m.content)})
    return out


@router.get("/models")
def list_models():
    """Lista el modelo del agente (formato OpenAI-compatible)."""
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
    """Conversa con el agente. Devuelve la respuesta (streaming o JSON)."""
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # Reanudar un interrupt (confirmación humana) o iniciar turno nuevo.
    if req.resume is not None:
        try:
            result = conversational_graph.invoke(
                Command(resume=req.resume.model_dump()),
                config=config,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Error al reanudar: {str(e)[:200]}")
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
            raise HTTPException(status_code=500, detail=f"Error del agente: {str(e)[:200]}")

    # Extraer el texto de la respuesta final.
    final_messages = result.get("messages", [])
    content = ""
    if final_messages:
        last = final_messages[-1]
        content = getattr(last, "content", "") or ""

    # Detectar si quedó un interrupt pendiente (confirmación humana).
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
            # Chunk de rol (inicio de la respuesta).
            yield (
                "data: " + json.dumps({
                    "id": payload["id"],
                    "object": "chat.completion.chunk",
                    "model": req.model,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                }, ensure_ascii=False) + "\n\n"
            )
            # Emitir el contenido en trozos (compatible con OpenAI streaming).
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
