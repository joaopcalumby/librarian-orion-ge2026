import io
import json
import logging
from datetime import datetime, timezone

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)


def get_minio_client(endpoint: str, access_key: str, secret_key: str, secure: bool) -> Minio:
    return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)


def ensure_bucket(client: Minio, bucket_name: str) -> None:
    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
            logger.info("Bucket '%s' criado.", bucket_name)
        else:
            logger.debug("Bucket '%s' já existe.", bucket_name)
    except S3Error as e:
        logger.error("Erro ao verificar/criar bucket '%s': %s", bucket_name, e)
        raise


def _pdf_object_name(session_id: str, arxiv_id: str) -> str:
    return f"sessions/{session_id}/pdfs/{arxiv_id}.pdf"


def _metadata_object_name(session_id: str, arxiv_id: str) -> str:
    return f"sessions/{session_id}/metadata/{arxiv_id}.json"


def upload_pdf(
    client: Minio,
    bucket_name: str,
    session_id: str,
    arxiv_id: str,
    pdf_bytes: bytes,
) -> str:

    object_name = _pdf_object_name(session_id, arxiv_id)
    client.put_object(
        bucket_name,
        object_name,
        data=io.BytesIO(pdf_bytes),
        length=len(pdf_bytes),
        content_type="application/pdf",
    )
    logger.info("[MinIO] PDF uploaded: %s (%d bytes)", object_name, len(pdf_bytes))
    return object_name


def upload_metadata(
    client: Minio,
    bucket_name: str,
    session_id: str,
    arxiv_id: str,
    paper: dict,
) -> str:

    object_name = _metadata_object_name(session_id, arxiv_id)

    enriched = {
        **paper,
        "_session_id": session_id,
        "_ingested_at": datetime.now(timezone.utc).isoformat(),
        "_pdf_object": _pdf_object_name(session_id, arxiv_id),
    }
    content = json.dumps(enriched, ensure_ascii=False, indent=2).encode("utf-8")

    client.put_object(
        bucket_name,
        object_name,
        data=io.BytesIO(content),
        length=len(content),
        content_type="application/json",
    )
    logger.info("[MinIO] metadata uploaded: %s", object_name)
    return object_name
