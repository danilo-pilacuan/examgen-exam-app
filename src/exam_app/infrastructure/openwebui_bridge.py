"""Compatibility bridge: re-exports the OpenWebUI client.

Tools in `agents_backend.conversational` import from here
(`exam_app.infrastructure.openwebui_bridge`) to keep the naming convention
used before the restructuring (`src.api.openwebui`).

The real client lives in `exam_app.infrastructure.openwebui_client`.
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
