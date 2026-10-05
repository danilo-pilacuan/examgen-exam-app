"""Puente de compatibilidad: reexporta el cliente OpenWebUI.

Las tools de `agents_backend.conversational` importan de aquí
(`exam_app.infrastructure.openwebui_bridge`) para mantener la convención de
nombres que usaban antes de la reestructuración (`src.api.openwebui`).

El cliente real vive en `exam_app.infrastructure.openwebui_client`.
"""
from exam_app.infrastructure.openwebui_client import (  # noqa: F401
    build_attachment_markdown,
    download_file_content,
    get_file_metadata,
    upload_file_to_openwebui,
)

__all__ = [
    "upload_file_to_openwebui",
    "build_attachment_markdown",
    "get_file_metadata",
    "download_file_content",
]
