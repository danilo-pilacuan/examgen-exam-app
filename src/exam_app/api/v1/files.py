"""Own attachment endpoint: POST /api/files.

The frontend uploads the file here (drag & drop); exam-app saves it, extracts
the text and indexes it as material of the subject. The `<file id=.../>` tag
is kept as convention so `add_material_file` keeps working with OpenWebUI
attachments (legacy).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session as OrmSession

from core.domain.auth import get_current_user, get_db
from core.domain.models import Subject, User

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
    user: User = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    """Uploads a file: extract → save → index (RAG)."""
    if file.filename is None:
        raise HTTPException(status_code=400, detail="File has no name")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # The target subject must belong to the professor (or be shared).
    if subject_id:
        try:
            sid = uuid.UUID(subject_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid subject_id")
        subject = db.get(Subject, sid)
        if subject is None:
            raise HTTPException(status_code=404, detail="Subject not found")
        if subject.owner_id is not None and subject.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Subject belongs to another professor")

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
