"""Implementación de FileServicePort: extraer → guardar → indexar.

Reutiliza `core.domain.bank.material` (indexación RAG) y
`core.infrastructure.files.extract` (extracción multi-formato).
"""
from __future__ import annotations

import uuid
from pathlib import Path

from core.domain.bank.material import (
    extract_file_text,
    index_text_for_subject,
    save_file_to_subject_folder,
)

from exam_app.domain.ports import FileResult


class LocalFileService:
    """FileServicePort sobre el sistema de archivos local + BD."""

    async def upload(
        self,
        filename: str,
        content: bytes,
        subject_id: str | None = None,
        subject_name: str | None = None,
        index: bool = True,
    ) -> FileResult:
        """Procesa un archivo subido: guarda, extrae e indexa (RAG)."""
        import asyncio

        file_id = str(uuid.uuid4())
        # Guardar el archivo físico en el repositorio de material.
        saved_path: Path | None = None
        if subject_name:
            try:
                saved_path = await asyncio.to_thread(
                    save_file_to_subject_folder, content, filename, subject_name
                )
            except Exception:  # noqa: BLE001
                saved_path = None

        # Extraer texto (a un temporal si no se guardó físicamente).
        tmp_path = Path(f"/tmp/exam_app_{file_id}_{filename}")
        target = saved_path or tmp_path
        target.write_bytes(content)

        try:
            text = await asyncio.to_thread(extract_file_text, target)
        finally:
            if saved_path is None:
                tmp_path.unlink(missing_ok=True)

        chunks = 0
        if index and subject_id and text.strip():
            res = await asyncio.to_thread(
                index_text_for_subject,
                text,
                subject_id,
                f"upload:{filename}",
                False,
            )
            chunks = int(res.get("chunks_guardados", 0))

        return FileResult(
            file_id=file_id,
            filename=filename,
            chunks_indexed=chunks,
            indexed=chunks > 0,
            tag=f'<file id="{file_id}" name="{filename}"/>',
        )
