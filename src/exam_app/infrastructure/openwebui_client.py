"""Open WebUI integration for file uploads (native attachments).

When the agent generates a PDF (exam), it uploads it to Open WebUI via its
file API (`/api/v1/files/`). This way the PDF appears as a NATIVE downloadable
attachment in the chat, instead of an external HTTP link in plain text.

Requirements:
  - Open WebUI must be reachable from exam-app (same Docker network).
  - Authentication uses the default admin user (WEBUI_AUTH=false).
"""
from __future__ import annotations

from pathlib import Path

import requests

from core.config import settings


def _login_token() -> str:
    """Gets a JWT token from Open WebUI with the admin user."""
    resp = requests.post(
        f"{settings.openwebui_url}/api/v1/auths/signin",
        json={
            "email": settings.openwebui_admin_email,
            "password": settings.openwebui_admin_password,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def upload_file_to_openwebui(
    pdf_path: str | Path,
    filename: str | None = None,
    mime_type: str = "application/pdf",
) -> dict:
    """Uploads a file to Open WebUI and returns the result (id, path, etc.).

    Reusable for exam PDFs and CSV/XLSX bank exports.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"File does not exist: {pdf_path}")

    token = _login_token()
    headers = {"Authorization": f"Bearer {token}"}
    fname = filename or pdf_path.name

    with open(pdf_path, "rb") as f:
        files = {"file": (fname, f, mime_type)}
        resp = requests.post(
            f"{settings.openwebui_url}/api/v1/files/",
            headers=headers,
            files=files,
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json()


def build_attachment_markdown(
    upload_result: dict,
    title: str = "📄 **Exam PDF attached to the chat.**",
    label: str = "Download the PDF",
) -> str:
    """Builds the markdown with the link to the native Open WebUI attachment.

    Open WebUI serves uploaded files at:
      /api/v1/files/{file_id}/content

    `title` and `label` allow reusing the format for other attachments
    (e.g. CSV/XLSX exports of the question bank).
    """
    file_id = upload_result.get("id", "")
    filename = upload_result.get("filename", "file")
    # Public URL accessible from the user's browser.
    content_url = f"{settings.openwebui_public_url}/api/v1/files/{file_id}/content"
    return (
        f"{title}\n"
        f"File: **{filename}**\n"
        f"📥 [{label}]({content_url})\n\n"
        f"*Also available at: `{content_url}`*"
    )


def get_file_metadata(file_id: str) -> dict:
    """Gets the metadata of an Open WebUI file (name, type, content).

    Returns the file dict as exposed by `/api/v1/files/{id}`.
    If the file does not exist or cannot be accessed, raises an exception.
    """
    token = _login_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        f"{settings.openwebui_url}/api/v1/files/{file_id}",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def download_file_content(file_id: str) -> bytes:
    """Downloads the binary of an Open WebUI file.

    If the file has no accessible binary content or the download fails, raises
    an exception with a clear message.
    """
    token = _login_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        f"{settings.openwebui_url}/api/v1/files/{file_id}/content",
        headers=headers,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content

