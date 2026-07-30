"""
Cliente PostgreSQL para o Ouro (texto).

Schema:
    papers          -- uma linha por paper (dedupado por arxiv_id, global)
    chunks          -- uma linha por chunk (PK chunk_id; FK -> papers)
    chunks_with_meta -- VIEW que junta chunks + papers para consulta direta
                       por chunk_id (atende o spec gold-storage cenário
                       "Localização do texto a partir do ID do vetor").

Sessões diferentes inserem chunks novos (com chunk_id novo) mas reaproveitam
a linha existente em `papers` se o `arxiv_id` já tiver sido visto.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterable, Iterator

import psycopg
from psycopg.rows import dict_row

from config import settings

logger = logging.getLogger(__name__)


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS papers (
    arxiv_id      TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    authors       TEXT[] NOT NULL,
    abstract      TEXT,
    published     TIMESTAMPTZ,
    arxiv_url     TEXT NOT NULL,
    pdf_url       TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    UUID PRIMARY KEY,
    arxiv_id    TEXT NOT NULL REFERENCES papers(arxiv_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (arxiv_id, session_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_arxiv_id  ON chunks(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_chunks_session   ON chunks(session_id);

CREATE OR REPLACE VIEW chunks_with_meta AS
SELECT
    c.chunk_id,
    c.arxiv_id,
    p.title,
    p.authors,
    p.arxiv_url,
    c.chunk_index,
    c.chunk_text,
    c.session_id,
    c.created_at
FROM chunks c
JOIN papers p USING (arxiv_id);
"""


def _conninfo() -> str:
    return (
        f"host={settings.POSTGRES_HOST} port={settings.POSTGRES_PORT} "
        f"dbname={settings.POSTGRES_DB} user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD}"
    )


def connect() -> psycopg.Connection:
    """Abre uma conexão nova. Caller é responsável por fechar."""
    return psycopg.connect(_conninfo(), row_factory=dict_row)


@contextmanager
def session() -> Iterator[psycopg.Connection]:
    """Context manager que abre, commita e fecha a conexão."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: psycopg.Connection) -> None:
    """Cria tabelas, índices e view se ainda não existirem. Idempotente."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_DDL)
    logger.info("[Postgres] schema garantido (papers, chunks, chunks_with_meta).")


def upsert_paper(conn: psycopg.Connection, paper: dict) -> None:
    """
    Insere o paper se ainda não existe. Se já existe (mesmo `arxiv_id`),
    não toca — assumimos que metadados do arXiv são estáveis.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO papers (arxiv_id, title, authors, abstract, published, arxiv_url, pdf_url)
            VALUES (%(arxiv_id)s, %(title)s, %(authors)s, %(abstract)s, %(published)s, %(arxiv_url)s, %(pdf_url)s)
            ON CONFLICT (arxiv_id) DO NOTHING
            """,
            {
                "arxiv_id": paper["arxiv_id"],
                "title": paper["title"],
                "authors": list(paper.get("authors", [])),
                "abstract": paper.get("abstract"),
                "published": paper.get("published"),
                "arxiv_url": paper["arxiv_url"],
                "pdf_url": paper.get("pdf_url"),
            },
        )


def insert_chunks(
    conn: psycopg.Connection,
    chunks: Iterable[dict],
    session_id: str,
) -> int:
    """
    Insere um lote de chunks. Retorna o número de linhas inseridas.

    Falha com erro de constraint se algum `chunk_id` já existir — o spec
    proíbe sobrescrita silenciosa. O caller deve garantir que `upsert_paper`
    rodou antes para o `arxiv_id` referenciado existir.
    """
    rows = [
        {
            "chunk_id": c["chunk_id"],
            "arxiv_id": c["arxiv_id"],
            "chunk_index": c["chunk_index"],
            "chunk_text": c["chunk_text"],
            "session_id": session_id,
        }
        for c in chunks
    ]
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks (chunk_id, arxiv_id, chunk_index, chunk_text, session_id)
            VALUES (%(chunk_id)s, %(arxiv_id)s, %(chunk_index)s, %(chunk_text)s, %(session_id)s)
            """,
            rows,
        )
    logger.info("[Postgres] inseridos %d chunks (session=%s).", len(rows), session_id)
    return len(rows)


def fetch_chunk_by_id(conn: psycopg.Connection, chunk_id: str) -> dict | None:
    """Recupera um chunk completo (com metadados do paper) pelo `chunk_id`."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM chunks_with_meta WHERE chunk_id = %s", (chunk_id,))
        return cur.fetchone()


def fetch_chunks_by_ids(
    conn: psycopg.Connection,
    chunk_ids: Iterable[str],
) -> dict[str, dict]:
    """
    Recupera vários chunks de uma vez, indexados por `chunk_id`.

    Uma query em vez de N. Chunks sem linha correspondente simplesmente não
    aparecem no retorno: runs antigos deixaram pontos órfãos no Qdrant, e a
    busca não deve quebrar por causa deles.
    """
    ids = list(chunk_ids)
    if not ids:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM chunks_with_meta WHERE chunk_id = ANY(%s::uuid[])",
            (ids,),
        )
        return {str(row["chunk_id"]): row for row in cur.fetchall()}
