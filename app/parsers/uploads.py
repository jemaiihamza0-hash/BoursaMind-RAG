"""Safe upload parsing for text and PDF documents."""

from __future__ import annotations

from io import BytesIO
from pathlib import PurePath

from fastapi import HTTPException, UploadFile

from app.config import get_settings


async def read_upload_text(file: UploadFile) -> tuple[str, str]:
    settings = get_settings()
    filename = PurePath(file.filename or "document.txt").name
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    if suffix not in settings.allowed_extensions:
        raise HTTPException(status_code=415, detail=f"Unsupported file extension: {suffix}")
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large.")
    if suffix == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(data))
            pages = [(page.extract_text() or "") for page in reader.pages]
            return "\f".join(pages).strip(), filename
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="PDF parsing failed.") from exc
    try:
        return data.decode("utf-8"), filename
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Text upload must be UTF-8.") from exc
