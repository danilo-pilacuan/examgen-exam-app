"""Dependencias FastAPI (inyección de puertos y sesiones).

Los routers piden aquí sus dependencias; las implementaciones concretas se
resuelven en un solo lugar (fácil de sustituir por dobles en pruebas).
"""
from __future__ import annotations

from functools import lru_cache

from exam_app.core.config import settings
from exam_app.domain.ports import ChatEnginePort, FileServicePort
from exam_app.services.chat_engine import ChatService
from exam_app.services.event_bus import EventBus


@lru_cache
def get_event_bus() -> EventBus:
    return EventBus()


@lru_cache
def get_chat_engine() -> ChatEnginePort:
    from exam_app.infrastructure.langgraph_engine import LangGraphEngine

    return LangGraphEngine(get_event_bus())


@lru_cache
def get_chat_service() -> ChatService:
    return ChatService(get_chat_engine(), get_event_bus())


@lru_cache
def get_file_service() -> FileServicePort:
    from exam_app.infrastructure.local_file_service import LocalFileService

    return LocalFileService()


def get_settings() -> settings.__class__:
    return settings
