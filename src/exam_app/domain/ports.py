"""Puertos (interfaces) de exam-app: desacoplan la API de las implementaciones.

Los routers consumen estos puertos vía `services/`; las implementaciones
concretas viven en `infrastructure/` (LangGraph, sistema de archivos, etc.).
Esto permite testear exam-app con dobles de prueba.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from exam_app.schemas.events import SSEEvent


@dataclass
class FileResult:
    """Resultado de la subida/procesamiento de un archivo."""

    file_id: str
    filename: str
    chunks_indexed: int = 0
    indexed: bool = False
    tag: str = ""


class ChatEnginePort(Protocol):
    """Motor de chat: orquesta el grafo conversacional."""

    async def stream(
        self,
        message: str,
        thread_id: str,
        subject_id: str | None = None,
        subject_name: str | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Ejecuta un turno y emite eventos SSE (tokens + eventos tipados)."""
        ...

    async def invoke(
        self,
        message: str,
        thread_id: str,
        subject_id: str | None = None,
        subject_name: str | None = None,
    ) -> dict[str, Any]:
        """Ejecuta un turno completo (sin streaming) y devuelve el resultado."""
        ...

    def cancel(self, thread_id: str) -> None:
        """Marca el thread como cancelado (la respuesta se descarta)."""
        ...


class FileServicePort(Protocol):
    """Servicio de archivos: extraer → guardar → indexar."""

    async def upload(
        self,
        filename: str,
        content: bytes,
        subject_id: str | None = None,
        index: bool = True,
    ) -> FileResult:
        """Procesa un archivo subido y (opcionalmente) lo indexa como material."""
        ...
