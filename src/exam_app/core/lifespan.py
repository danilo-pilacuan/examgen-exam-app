"""exam-app lifespan: resource initialization and shutdown.

- Initializes the LangGraph checkpointer (Postgres) at startup and closes it
  at shutdown.
- Prepares the output directory shared with latex-compiler.
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI

from core.infrastructure.files.output_paths import get_output_dir


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown of the application (FastAPI lifespan)."""
    # --- startup ---
    get_output_dir().mkdir(parents=True, exist_ok=True)
    app.state.checkpointer = None
    try:
        yield
    finally:
        # --- shutdown ---
        checkpointer = getattr(app.state, "checkpointer", None)
        if checkpointer is not None:
            conn = getattr(checkpointer, "conn", None)
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
        app.state.checkpointer = None
