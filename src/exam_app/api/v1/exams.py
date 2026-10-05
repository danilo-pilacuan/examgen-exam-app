"""REST endpoints for generated exams and indexed material.

- GET    /subjects/{id}/exams    : exam history (by naming convention of
                                   PDFs in the output directory)
- GET    /subjects/{id}/material : indexed material chunks
- DELETE /subjects/{id}/material : delete material (by source or all)
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from core.domain.models import Subject, MaterialChunk
from core.infrastructure.db.connection import SessionLocal

from exam_app.schemas.bank import MaterialChunkOut, MaterialDeleteRequest

router = APIRouter(tags=["exams-material"])


# ---------------------------------------------------------------------------
# Exams
# ---------------------------------------------------------------------------
@router.get("/subjects/{subject_id}/exams")
def list_subject_exams(subject_id: str):
    """Lists the generated exams of a subject.

    Exams are detected by the PDFs in the output directory (shared with the
    latex-compiler). Grouped by timestamp of the name.
    """
    try:
        sid = uuid.UUID(subject_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subject_id")

    db = SessionLocal()
    try:
        subject = db.get(Subject, sid)
        if subject is None:
            raise HTTPException(status_code=404, detail="Subject not found")
    finally:
        db.close()

    from core.infrastructure.files.output_paths import get_output_dir
    from pathlib import Path

    output_dir = get_output_dir()
    if not output_dir.exists():
        return []

    # Group PDFs by prefix (timestamp) — naming convention of the
    # latex-compiler: examen_<subject>_<timestamp>_v<N>.pdf
    exams_by_prefix: dict[str, list[Path]] = {}
    for pdf in sorted(output_dir.glob("*.pdf")):
        name = pdf.name
        # Extract the timestamp (last part before _vN)
        parts = name.rsplit("_v", 1)
        if len(parts) == 2 and parts[1].split(".")[0].isdigit():
            prefix = parts[0]
        else:
            prefix = name.rsplit(".", 1)[0]
        exams_by_prefix.setdefault(prefix, []).append(pdf)

    exams = []
    for prefix, pdfs in sorted(exams_by_prefix.items(), reverse=True):
        pdfs.sort()
        exams.append(
            {
                "id": prefix,
                "subject_id": subject_id,
                "subject_name": subject.name,
                "blueprint": {},
                "items": [],
                "pdf_paths": [p.name for p in pdfs],
                "created_at": None,
            }
        )
    return exams


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------
@router.get("/subjects/{subject_id}/material", response_model=list[MaterialChunkOut])
def list_subject_material(
    subject_id: str,
    source_file: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """Lists the indexed material chunks of a subject."""
    try:
        sid = uuid.UUID(subject_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subject_id")

    db = SessionLocal()
    try:
        if db.get(Subject, sid) is None:
            raise HTTPException(status_code=404, detail="Subject not found")

        stmt = (
            select(MaterialChunk)
            .where(MaterialChunk.subject_id == sid)
            .order_by(MaterialChunk.source_file, MaterialChunk.chunk_index)
            .limit(limit)
        )
        if source_file:
            stmt = stmt.where(MaterialChunk.source_file == source_file)

        chunks = list(db.execute(stmt).scalars().all())
        return [
            MaterialChunkOut(
                id=str(c.id),
                source_file=c.source_file,
                chunk_index=c.chunk_index,
                content=c.content,
            )
            for c in chunks
        ]
    finally:
        db.close()


@router.delete("/subjects/{subject_id}/material")
def delete_subject_material(subject_id: str, req: MaterialDeleteRequest):
    """Deletes indexed material (by source or all of the subject)."""
    try:
        sid = uuid.UUID(subject_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subject_id")

    if not req.delete_all and not req.source_file:
        raise HTTPException(
            status_code=400,
            detail="Specify source_file or delete_all=true",
        )

    db = SessionLocal()
    try:
        if db.get(Subject, sid) is None:
            raise HTTPException(status_code=404, detail="Subject not found")

        stmt = select(MaterialChunk).where(MaterialChunk.subject_id == sid)
        if req.source_file:
            stmt = stmt.where(MaterialChunk.source_file == req.source_file)

        chunks = list(db.execute(stmt).scalars().all())
        for c in chunks:
            db.delete(c)
        db.commit()
        return {"deleted_chunks": len(chunks)}
    finally:
        db.close()
