"""Endpoint de adjuntos propios: POST /api/files.

El frontend sube el archivo aquí (drag & drop); exam-app lo guarda, extrae
el texto y lo indexa como material de la materia. El tag `<file id=.../>`
se mantiene como convención para que `agregar_material_archivo` siga
funcionando con adjuntos de OpenWebUI (legado).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from exam_app.api.deps import get_file_service
from exam_app.domain.ports import FileServicePort

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    subject_id: str | None = Form(default=None),
    subject_name: str | None = Form(default=None),
    index: bool = Form(default=True),
    service: FileServicePort = Depends(get_file_service),
):
    """Sube un archivo: extraer → guardar → indexar (RAG)."""
    if file.filename is None:
        raise HTTPException(status_code=400, detail="Archivo sin nombre")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    result = await service.upload(
        filename=file.filename,
        content=content,
        subject_id=subject_id,
        subject_name=subject_name,
        index=index,
    )
    return {
        "file_id": result.file_id,
        "filename": result.filename,
        "subject_id": subject_id,
        "chunks_indexed": result.chunks_indexed,
        "indexed": result.indexed,
        "tag": result.tag,
    }
