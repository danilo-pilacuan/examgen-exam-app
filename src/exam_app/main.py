"""API for exam generator delivery (exam-app).

Endpoints:
  - POST /api/chat/stream     : chat with SSE streaming (typed events)
  - POST /api/chat            : chat without streaming
  - POST /api/files           : upload of own attachments (extract→index)
  - WS   /ws                  : notification and cancellation bus
  - GET/POST /subjects       : list / register subjects
  - GET /subjects/{id}/questions : question bank (filters)
  - PUT /questions/{id}/verification : human verification (HITL)
  - GET /subjects/{id}/exams : exam history
  - GET/DELETE /subjects/{id}/material : indexed material
  - POST /subjects/{id}/index : index material (RAG) of a subject
  - POST /exams/generate      : generate an exam from a blueprint
  - POST /v1/chat/completions : OpenAI-compatible agent (OpenWebUI legacy)
  - GET  /health              : service health

Usage (development, with packages installed in editable mode):
    uvicorn exam_app.main:app --host 0.0.0.0 --port 8000
"""
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core.infrastructure.db.connection import SessionLocal  # noqa: E402
from core.domain.models import Subject, MaterialChunk  # noqa: E402
from core.domain.schemas.exam import ExamBlueprint  # noqa: E402
from core.infrastructure.latex.templates.registry import TemplateId  # noqa: E402
from exam_app.api.deps import get_chat_service  # noqa: E402
from exam_app.api.v1.chat import router as chat_api_router  # noqa: E402
from exam_app.api.v1.files import router as files_router  # noqa: E402
from exam_app.api.v1.legacy_openai import router as chat_router  # noqa: E402
from exam_app.api.v1.subjects import router as subjects_router  # noqa: E402
from exam_app.api.v1.exams import router as exams_material_router  # noqa: E402
from exam_app.api.v1.auth import router as auth_router  # noqa: E402
from exam_app.api.v1.ws import router as ws_router  # noqa: E402
from exam_app.core.config import settings as app_settings  # noqa: E402
from exam_app.core.lifespan import lifespan  # noqa: E402

app = FastAPI(title="Exam Generator API", version="0.2.0", lifespan=lifespan)

# CORS: the frontend (Vite dev / nginx) calls the API from another origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conversational agent router (OpenAI-compatible for OpenWebUI).
app.include_router(chat_router)
# Frontend's own routers (stage 2: reactive backend).
app.include_router(chat_api_router)
app.include_router(files_router)
app.include_router(ws_router)
# REST routers for subjects, bank, exams and material (stage 5).
app.include_router(subjects_router)
app.include_router(exams_material_router)
# Auth: professor accounts and JWT sessions (multi-tenant).
app.include_router(auth_router)

# Output directory for PDFs (shared with latex-compiler via volume).
# In the container it is mounted at /app/output; locally it is data/agent_outputs.
from core.infrastructure.files.output_paths import get_output_dir  # noqa: E402

OUTPUT_DIR = get_output_dir()


# File types servable by GET /output (exam PDFs, exports, exam JSON, ZIP).
_ALLOWED_OUTPUT_MIME = {
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".json": "application/json",
    ".zip": "application/zip",
}


@app.api_route("/output/{filename}", methods=["GET", "HEAD"])
def download_output(filename: str):
    """Serves a generated file (exam PDF, CSV/XLSX export).

    HEAD is supported for pre-flight checks (download managers, antivirus).
    """
    # Prevent path traversal: only the base name of the file.
    safe = Path(filename).name
    file_path = OUTPUT_DIR / safe
    mime = _ALLOWED_OUTPUT_MIME.get(file_path.suffix.lower())
    if not file_path.exists() or mime is None:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        file_path,
        media_type=mime,
        filename=safe,
    )


# ---------------------------------------------------------------------------
# Input schemas (indexing and exam generation endpoints)
# ---------------------------------------------------------------------------
class IndexRequest(BaseModel):
    materials: list[str] = Field(
        ..., description="Glob patterns or paths to material files"
    )
    clear: bool = False


class ExamRequest(BaseModel):
    subject_id: str
    blueprint: ExamBlueprint
    template_id: str = "base_exam"
    persist: bool = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/subjects/{subject_id}/index")
def index_subject(subject_id: str, req: IndexRequest):
    """Indexes the material of a subject (generates chunks + embeddings)."""
    from scripts.index_material import chunk_text, extract_file, resolve_materials

    files = resolve_materials(req.materials)
    if not files:
        raise HTTPException(status_code=400, detail="No files found")

    all_chunks: list[tuple[str, int, str]] = []
    for f in files:
        try:
            text = extract_file(f)
            chunks = chunk_text(text)
            for i, c in enumerate(chunks):
                all_chunks.append((f, i, c))
        except Exception as e:
            print(f"  ⚠️ {f}: {e}")

    if not all_chunks:
        raise HTTPException(status_code=400, detail="Could not extract text")

    from core.infrastructure.embeddings.embedder import embedder
    from core.domain.models import MaterialChunk

    contents = [c for _, _, c in all_chunks]
    vectors = embedder.embed(contents)

    db = SessionLocal()
    try:
        sid = uuid.UUID(subject_id)
        subject = db.get(Subject, sid)
        if subject is None:
            raise HTTPException(status_code=404, detail="Subject does not exist")

        if req.clear:
            db.query(MaterialChunk).filter(MaterialChunk.subject_id == sid).delete()

        for (src, idx, content), vec in zip(all_chunks, vectors):
            db.add(MaterialChunk(
                subject_id=sid, source_file=src, chunk_index=idx,
                content=content, embedding=vec,
            ))
        db.commit()
        return {"subject_id": subject_id, "indexed_chunks": len(all_chunks)}
    finally:
        db.close()


@app.post("/exams/generate")
def generate_exam(req: ExamRequest):
    """Generates an exam from a blueprint."""
    from agents_backend.exam_pipeline.graph import run_exam_pipeline

    # Resolve template_id
    try:
        template = TemplateId(req.template_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid template: {req.template_id}")

    # Fail fast (BUG-027): refuse to generate an exam for a subject with no
    # indexed material AND no bank questions. Otherwise the pipeline generates
    # generic items from the LLM's general knowledge and publishes them as a
    # real exam. Mirrors the BUG-004/C6 fail-fast guard (empty exams) and the
    # E5 criterion of the test plan.
    db = SessionLocal()
    try:
        subject = db.get(Subject, uuid.UUID(req.subject_id))
        if subject is None:
            raise HTTPException(status_code=404, detail="Subject not found")
        subject_name = subject.name
        from sqlalchemy import func as sa_func

        chunks_count = (
            db.query(sa_func.count(MaterialChunk.id))
            .filter(MaterialChunk.subject_id == subject.id)
            .scalar()
            or 0
        )
        questions_count = (
            db.execute(
                text("SELECT count(*) FROM generated_questions WHERE subject_id = :sid"),
                {"sid": subject.id},
            ).scalar()
            or 0
        )
        if chunks_count == 0 and questions_count == 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This subject has no indexed material and no questions in the bank. "
                    "Upload and index material (or generate questions first); otherwise "
                    "the exam would be generated from generic knowledge, not from the subject."
                ),
            )
    finally:
        db.close()

    result = run_exam_pipeline(
        subject_id=req.subject_id,
        subject_name=subject_name,
        template_id=template,
        raw_materials_paths=[],  # RAG uses the DB, not direct files
        blueprint=req.blueprint,
        persist=req.persist,
    )
    return result
