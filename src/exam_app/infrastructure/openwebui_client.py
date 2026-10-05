"""Integración con Open WebUI para subir archivos (adjuntos nativos).

Cuando el agente genera un PDF (examen), lo sube a Open WebUI vía su API de
archivos (`/api/v1/files/`). Así el PDF aparece como un adjunto descargable
NATIVO en el chat, en vez de un enlace HTTP externo en texto plano.

Requisitos:
  - Open WebUI debe estar accesible desde el exam-app (misma red Docker).
  - Se autentica con el usuario admin por defecto (WEBUI_AUTH=false).
"""
from __future__ import annotations

from pathlib import Path

import requests

from core.config import settings


def _login_token() -> str:
    """Obtiene un token JWT de Open WebUI con el usuario admin."""
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
    """Sube un archivo a Open WebUI y devuelve el resultado (id, path, etc.).

    Reutilizable para PDFs de exámenes y exportaciones CSV/XLSX del banco.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {pdf_path}")

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
    titulo: str = "📄 **Examen en PDF adjunto al chat.**",
    etiqueta: str = "Descargar el PDF",
) -> str:
    """Construye el markdown con el enlace al adjunto nativo de Open WebUI.

    Open WebUI sirve los archivos subidos en:
      /api/v1/files/{file_id}/content

    `titulo` y `etiqueta` permiten reutilizar el formato para otros adjuntos
    (ej. exportaciones CSV/XLSX del banco de preguntas).
    """
    file_id = upload_result.get("id", "")
    filename = upload_result.get("filename", "archivo")
    # URL pública accesible desde el navegador del usuario.
    content_url = f"{settings.openwebui_public_url}/api/v1/files/{file_id}/content"
    return (
        f"{titulo}\n"
        f"Archivo: **{filename}**\n"
        f"📥 [{etiqueta}]({content_url})\n\n"
        f"*También disponible en: `{content_url}`*"
    )


def get_file_metadata(file_id: str) -> dict:
    """Obtiene la metadata de un archivo de Open WebUI (nombre, tipo, contenido).

    Devuelve el dict del archivo tal como lo expone `/api/v1/files/{id}`.
    Si el archivo no existe o no se puede acceder, lanza una excepción.
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
    """Descarga el binario de un archivo de Open WebUI.

    Si el archivo no tiene contenido binario accesible o falla, lanza una
    excepción con un mensaje claro.
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

