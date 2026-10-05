"""Own attachment endpoint: POST /api/files.

The frontend uploads the file here (drag & drop); exam-app saves it, extracts
the text and indexes it as material of the subject. The `<file id=.../>` tag
is kept as convention so `add_material_file` keeps working with OpenWebUI
attachments (legacy).
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
    """Uploads a file: extract → save → index (RAG)."""
    if file.filename is None:
        raise HTTPException(status_code=400, detail="File has no name")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

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
