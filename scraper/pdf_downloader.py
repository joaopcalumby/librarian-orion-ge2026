from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests
from minio import Minio
from minio.error import S3Error

from config import settings
from storage import minio_client

logger = logging.getLogger(__name__)


PDF_MAGIC = b"%PDF"
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_BACKOFF_BASE_SECONDS = 2.0
USER_AGENT = "librarian-orion-ge2026/0.1 (https://github.com/joaopcalumby/librarian-orion-ge2026)"


@dataclass
class DownloadResult:
    arxiv_id: str
    success: bool
    pdf_object: Optional[str] = None
    metadata_object: Optional[str] = None
    error: Optional[str] = None


class PdfValidationError(Exception):
    pass


def _fetch_pdf_bytes(pdf_url: str) -> bytes:
    last_exc: Optional[Exception] = None

    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        try:
            response = requests.get(
                pdf_url,
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
                headers={"User-Agent": USER_AGENT},
            )
            if response.status_code == 200:
                return response.content
            if 500 <= response.status_code < 600:
                raise requests.HTTPError(
                    f"{response.status_code} {response.reason}",
                    response=response,
                )
            response.raise_for_status()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            last_exc = e
            if attempt < DOWNLOAD_MAX_RETRIES:
                wait = DOWNLOAD_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Falha no download de %s (tentativa %d/%d): %s. Aguardando %.1fs...",
                    pdf_url, attempt, DOWNLOAD_MAX_RETRIES, e, wait,
                )
                time.sleep(wait)
            else:
                logger.error("Download de %s falhou após %d tentativas.", pdf_url, DOWNLOAD_MAX_RETRIES)

    assert last_exc is not None
    raise last_exc


def _validate_pdf_bytes(content: bytes) -> None:
    if not content.startswith(PDF_MAGIC):
        raise PdfValidationError(
            f"Conteúdo baixado não começa com {PDF_MAGIC!r} (primeiros 8 bytes: {content[:8]!r})"
        )


def download_paper(
    client: Minio,
    bucket: str,
    session_id: str,
    paper: dict,
) -> DownloadResult:
    arxiv_id = paper["arxiv_id"]
    pdf_url = paper["pdf_url"]

    try:
        pdf_bytes = _fetch_pdf_bytes(pdf_url)
        _validate_pdf_bytes(pdf_bytes)
    except (requests.RequestException, PdfValidationError) as e:
        logger.error("Paper %s falhou no download: %s", arxiv_id, e)
        return DownloadResult(arxiv_id=arxiv_id, success=False, error=str(e))

    try:
        pdf_obj = minio_client.upload_pdf(client, bucket, session_id, arxiv_id, pdf_bytes)
        meta_obj = minio_client.upload_metadata(client, bucket, session_id, arxiv_id, paper)
    except S3Error as e:
        logger.error("Paper %s falhou no upload Bronze: %s", arxiv_id, e)
        return DownloadResult(arxiv_id=arxiv_id, success=False, error=f"MinIO: {e}")

    return DownloadResult(
        arxiv_id=arxiv_id,
        success=True,
        pdf_object=pdf_obj,
        metadata_object=meta_obj,
    )


def download_papers(
    client: Minio,
    bucket: str,
    session_id: str,
    papers: list[dict],
) -> list[DownloadResult]:
    results: list[DownloadResult] = []
    total = len(papers)

    for i, paper in enumerate(papers):
        logger.info("[%d/%d] Baixando %s...", i + 1, total, paper["arxiv_id"])
        results.append(download_paper(client, bucket, session_id, paper))

        if i < total - 1:
            time.sleep(settings.ARXIV_REQUEST_DELAY_SECONDS)

    return results
