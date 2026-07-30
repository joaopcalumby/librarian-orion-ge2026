from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from contextlib import asynccontextmanager

import httpx
import trafilatura
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from api.sources import ALLOWED_HOSTS, host_of, is_allowed
from processing.chunker import chunk_text
from processing.pdf_extractor import PdfExtractionError, extract
from processing.vectorizer import vectorize_chunks, vectorize_query
from storage import postgres_client
from storage.qdrant_client import connect as qdrant_connect
from storage.qdrant_client import ensure_collection, search as qdrant_search, upsert_vectors

logger = logging.getLogger(__name__)


_qdrant = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _qdrant
    logger.info("Inicializando schemas e collections...")

    def _init():
        with postgres_client.session() as conn:
            postgres_client.init_schema(conn)
        client = qdrant_connect()
        ensure_collection(client)
        return client

    _qdrant = await asyncio.to_thread(_init)
    logger.info("Pronto: Postgres OK, Qdrant OK.")
    yield
    logger.info("Encerrando.")


app = FastAPI(title="Librarian API", version="1.0.5", lifespan=lifespan)


class IngestResponse(BaseModel):
    document_id: str = Field(..., description="ID interno (arxiv_id) do documento")
    source_type: str = Field(..., description="'pdf' ou 'site'")
    title: str
    chunks: int
    vectors: int
    session_id: str


class SiteRequest(BaseModel):
    url: str


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Pergunta ou tema a buscar")
    limit: int = Field(5, ge=1, le=20, description="Quantos trechos retornar")


class SearchHit(BaseModel):
    chunk_id: str
    score: float
    document_id: str = Field(..., description="ID interno do documento de origem")
    title: str
    authors: list[str]
    url: str = Field(..., description="Endereço do documento original, para citação")
    chunk_index: int
    text: str


class SearchResponse(BaseModel):
    query: str
    results: list[SearchHit]


def _new_session_id() -> str:
    return f"api-{uuid.uuid4().hex[:8]}"


def _ingest(document: dict, text: str, session_id: str) -> IngestResponse:
    if not text or not text.strip():
        raise HTTPException(status_code=422, detail="conteúdo extraído está vazio")

    chunks = chunk_text(text, document)
    if not chunks:
        raise HTTPException(status_code=422, detail="texto não gerou nenhum chunk")

    chunks_with_vecs = vectorize_chunks(chunks)

    with postgres_client.session() as conn:
        postgres_client.upsert_paper(conn, document)
        n_text = postgres_client.insert_chunks(conn, chunks_with_vecs, session_id=session_id)
    n_vec = upsert_vectors(_qdrant, chunks_with_vecs)

    return IngestResponse(
        document_id=document["arxiv_id"],
        source_type=document["_source_type"],
        title=document["title"],
        chunks=n_text,
        vectors=n_vec,
        session_id=session_id,
    )


def _pdf_document(pdf_bytes: bytes, filename: str | None) -> dict:
    digest = hashlib.sha256(pdf_bytes).hexdigest()[:12]
    title = filename or f"pdf-{digest}"
    if title.lower().endswith(".pdf"):
        title = title[:-4]
    return {
        "arxiv_id": f"pdf:{digest}",
        "title": title,
        "authors": [],
        "abstract": None,
        "published": None,
        "arxiv_url": f"upload://pdf/{digest}",
        "pdf_url": None,
        "_source_type": "pdf",
    }


def _site_document(url: str, extracted: dict) -> dict:
    host = host_of(url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    title = (extracted.get("title") or url).strip()
    author = extracted.get("author")
    authors = [author.strip()] if author and author.strip() else []
    return {
        "arxiv_id": f"site:{host}:{digest}",
        "title": title,
        "authors": authors,
        "abstract": extracted.get("description"),
        "published": extracted.get("date"),
        "arxiv_url": url,
        "pdf_url": None,
        "_source_type": "site",
    }


async def _fetch_html(url: str) -> str:
    # User-Agent de browser real: Medium e similares retornam 403 a clientes
    # que se identificam como bot/biblioteca.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.text
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"falha ao buscar URL: {e}") from e


def _search(query: str, limit: int) -> SearchResponse:
    """Busca semântica: vetoriza a query, consulta o Qdrant, hidrata no Postgres.

    O Qdrant guarda só o vetor e o `arxiv_id`; o texto e os metadados de
    citação (título, autores, URL) vivem na view `chunks_with_meta`. Um sem o
    outro não responde nada.
    """
    query_vector = vectorize_query(query)
    hits = qdrant_search(_qdrant, query_vector, limit=limit)
    if not hits:
        return SearchResponse(query=query, results=[])

    with postgres_client.session() as conn:
        rows = postgres_client.fetch_chunks_by_ids(conn, [h["chunk_id"] for h in hits])

    results = []
    for hit in hits:
        row = rows.get(hit["chunk_id"])
        if row is None:
            logger.warning("chunk %s existe no Qdrant mas não no Postgres; ignorado.",
                           hit["chunk_id"])
            continue
        results.append(
            SearchHit(
                chunk_id=hit["chunk_id"],
                score=hit["score"],
                document_id=row["arxiv_id"],
                title=row["title"],
                authors=list(row["authors"] or []),
                url=row["arxiv_url"],
                chunk_index=row["chunk_index"],
                text=row["chunk_text"],
            )
        )

    logger.info("busca %r: %d pontos no Qdrant, %d hidratados.",
                query, len(hits), len(results))
    return SearchResponse(query=query, results=results)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/sources")
async def sources() -> dict:
    return {"allowed_hosts": sorted(ALLOWED_HOSTS)}


@app.post("/search", response_model=SearchResponse)
async def search_chunks(req: SearchRequest) -> SearchResponse:
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query vazia")
    return await asyncio.to_thread(_search, req.query, req.limit)


@app.post("/pdf", response_model=IngestResponse)
async def ingest_pdf(file: UploadFile = File(...)) -> IngestResponse:
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="arquivo vazio")
    if not pdf_bytes.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="conteúdo não é um PDF válido (magic bytes)")

    document = _pdf_document(pdf_bytes, file.filename)
    session_id = _new_session_id()

    logger.info("[%s] recebendo PDF '%s' (%d bytes)",
                document["arxiv_id"], document["title"], len(pdf_bytes))

    try:
        text = await asyncio.to_thread(extract, pdf_bytes)
    except PdfExtractionError as e:
        raise HTTPException(status_code=422, detail=f"falha na extração: {e}") from e

    return await asyncio.to_thread(_ingest, document, text, session_id)


@app.post("/site", response_model=IngestResponse)
async def ingest_site(req: SiteRequest) -> IngestResponse:
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="url vazia")
    if not is_allowed(req.url):
        raise HTTPException(
            status_code=404,
            detail=f"site não mapeado. Domínios aceitos: {sorted(ALLOWED_HOSTS)}",
        )

    logger.info("Buscando %s", req.url)
    html = await _fetch_html(req.url)

    def _extract_article():
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        meta = trafilatura.extract_metadata(html)
        meta_dict = meta.as_dict() if meta else {}
        return text, meta_dict

    text, meta = await asyncio.to_thread(_extract_article)
    if not text:
        raise HTTPException(status_code=422, detail="não foi possível extrair conteúdo do HTML")

    document = _site_document(req.url, meta)
    session_id = _new_session_id()
    logger.info("[%s] site extraído (%d chars, título=%r)",
                document["arxiv_id"], len(text), document["title"])

    return await asyncio.to_thread(_ingest, document, text, session_id)
