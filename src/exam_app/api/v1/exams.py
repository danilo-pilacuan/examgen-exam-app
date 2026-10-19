"""REST endpoints for generated exams and indexed material.

- GET    /subjects/{id}/exams    : exam history (by naming convention of
                                   PDFs in the output directory)
- GET    /subjects/{id}/material : indexed material chunks
- DELETE /subjects/{id}/material : delete material (by source or all)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from core.domain.auth import get_current_user, get_db
from core.domain.models import Subject, MaterialChunk, User
from core.infrastructure.db.connection import SessionLocal

from exam_app.schemas.bank import MaterialChunkOut, MaterialDeleteRequest

router = APIRouter(tags=["exams-material"])


def _get_owned_subject(subject_id: str, user: User) -> uuid.UUID:
    """Resolves a subject the user may access; returns its UUID."""
    try:
        sid = uuid.UUID(subject_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subject_id")
    db = SessionLocal()
    try:
        subject = db.get(Subject, sid)
        if subject is None:
            raise HTTPException(status_code=404, detail="Subject not found")
        if subject.owner_id is not None and subject.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Subject belongs to another professor")
    finally:
        db.close()
    return sid


# ---------------------------------------------------------------------------
# Exams
# ---------------------------------------------------------------------------
@router.get("/subjects/{subject_id}/exams")
def list_subject_exams(subject_id: str, user: User = Depends(get_current_user)):
    """Lists the generated exams of a subject.

    Primary source: the `exams` table (every generation is persisted with
    its blueprint, items and artifact names). Legacy exams (PDFs in the
    output directory without a DB row) are listed from the files.
    """
    sid = _get_owned_subject(subject_id, user)

    db = SessionLocal()
    try:
        subject = db.get(Subject, sid)
        if subject is None:
            raise HTTPException(status_code=404, detail="Subject not found")

        from core.domain.models import Exam as ExamModel

        rows = (
            db.query(ExamModel)
            .filter(ExamModel.subject_id == sid)
            .order_by(ExamModel.created_at.desc())
            .all()
        )
        exams = [
            {
                "id": str(row.id),
                "subject_id": str(row.subject_id),
                "subject_name": subject.name,
                "name": row.name,
                "blueprint": row.blueprint or {},
                "items": row.items or [],
                "num_versions": row.num_versions,
                "pdf_paths": row.pdf_paths or [],
                "zip_path": row.zip_path,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

        # Legacy exams: PDFs in the output directory without a DB row.
        from core.infrastructure.files.output_paths import get_output_dir
        from pathlib import Path

        output_dir = get_output_dir()
        if output_dir.exists():
            persisted_pdfs = {p for e in exams for p in e["pdf_paths"]}
            exams_by_prefix: dict[str, list[Path]] = {}
            for pdf in sorted(output_dir.glob("*.pdf")):
                if pdf.name in persisted_pdfs:
                    continue
                name = pdf.name
                parts = name.rsplit("_v", 1)
                if len(parts) == 2 and parts[1].split(".")[0].isdigit():
                    prefix = parts[0]
                else:
                    prefix = name.rsplit(".", 1)[0]
                exams_by_prefix.setdefault(prefix, []).append(pdf)

            for prefix, pdfs in sorted(exams_by_prefix.items(), reverse=True):
                pdfs.sort()
                record = {
                    "id": prefix,
                    "subject_id": subject_id,
                    "subject_name": subject.name,
                    "name": prefix,
                    "blueprint": {},
                    "items": [],
                    "num_versions": 1,
                    "pdf_paths": [p.name for p in pdfs],
                    "zip_path": None,
                    "created_at": None,
                }
                # Enrich from a raw JSON artifact when it exists.
                json_path = output_dir / f"{prefix}.json"
                if not json_path.exists():
                    json_path = output_dir / f"exam_{prefix}.json"
                if json_path.exists():
                    try:
                        raw = json.loads(json_path.read_text(encoding="utf-8"))
                        record["blueprint"] = raw.get("blueprint") or {}
                        record["items"] = raw.get("items") or []
                        record["created_at"] = raw.get("generated_at")
                    except (OSError, ValueError):
                        pass
                if record["created_at"] is None:
                    first_pdf = next((p for p in pdfs if p.exists()), None)
                    if first_pdf is not None:
                        record["created_at"] = datetime.fromtimestamp(
                            first_pdf.stat().st_mtime, tz=timezone.utc
                        ).isoformat()
                zip_path = output_dir / f"exam_{prefix}.zip"
                if zip_path.exists():
                    record["zip_path"] = zip_path.name
                exams.append(record)

        exams.sort(key=lambda e: e["created_at"] or "", reverse=True)
        return exams
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------
@router.get("/subjects/{subject_id}/material", response_model=list[MaterialChunkOut])
def list_subject_material(
    subject_id: str,
    source_file: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(get_current_user),
):
    """Lists the indexed material chunks of a subject."""
    sid = _get_owned_subject(subject_id, user)

    db = SessionLocal()
    try:
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
def delete_subject_material(
    subject_id: str,
    req: MaterialDeleteRequest,
    user: User = Depends(get_current_user),
):
    """Deletes indexed material (by source or all of the subject)."""
    sid = _get_owned_subject(subject_id, user)

    if not req.delete_all and not req.source_file:
        raise HTTPException(
            status_code=400,
            detail="Specify source_file or delete_all=true",
        )

    from core.domain.material.service import delete_all_material, delete_source_file

    try:
        if req.delete_all:
            result = delete_all_material(sid, delete_physical_files=False)
            return {"deleted_chunks": result["deleted_chunks"]}
        result = delete_source_file(
            sid,
            req.source_file,
            delete_physical_file=False,
        )
        return {"deleted_chunks": result["deleted_chunks"]}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
