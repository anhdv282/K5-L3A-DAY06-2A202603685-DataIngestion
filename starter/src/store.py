"""PostgreSQL + pgvector store: idempotent upsert, pruning and filtered cosine search."""
from __future__ import annotations

from typing import Any

import psycopg

from .config import FILTERABLE_FIELDS
from .models import Chunk

_RESULT_COLUMNS = ("chunk_id", "index_name", "document_id", "source", "page", "chapter",
                   "chapter_title", "section", "article", "article_title", "char_start",
                   "char_end", "text")


def _vec(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL missing (copy starter/.env.example to starter/.env)")
        if not isinstance(dimensions, int) or dimensions <= 0:
            raise ValueError(f"invalid embedding dimensions: {dimensions!r}")
        self.database_url = database_url
        self.dimensions = dimensions

    def connect(self) -> psycopg.Connection:
        # Fails loudly if Docker/Postgres is down; there is no in-memory fallback.
        return psycopg.connect(self.database_url, connect_timeout=5)

    def create_schema(self) -> None:
        with self.connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id        TEXT PRIMARY KEY,
                    index_name      TEXT NOT NULL,
                    document_id     TEXT NOT NULL,
                    source          TEXT NOT NULL,
                    page            INTEGER NOT NULL,
                    chapter         INTEGER NOT NULL,
                    chapter_title   TEXT,
                    section         TEXT,
                    article         INTEGER,
                    article_title   TEXT,
                    char_start      INTEGER NOT NULL,
                    char_end        INTEGER NOT NULL,
                    text            TEXT NOT NULL,
                    content_hash    TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    embedding       vector({self.dimensions}) NOT NULL,
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")
            row = conn.execute("""
                SELECT atttypmod FROM pg_attribute
                WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'""").fetchone()
            if row and row[0] != self.dimensions:
                raise RuntimeError(f"table chunks has vector({row[0]}), model has {self.dimensions}")
            conn.execute("CREATE INDEX IF NOT EXISTS chunks_index_doc ON chunks (index_name, document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS chunks_index_chapter ON chunks (index_name, chapter)")

    def existing_hashes(self, index_name: str, model_name: str) -> dict[str, str]:
        """chunk_id -> content_hash for rows embedded with the same model."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT chunk_id, content_hash FROM chunks WHERE index_name = %s AND embedding_model = %s",
                (index_name, model_name)).fetchall()
        return dict(rows)

    def upsert(self, index_name: str, chunks: list[Chunk], vectors: list[list[float]],
               model_name: str) -> int:
        """Insert or update chunks by stable chunk_id (no duplicates on re-ingest)."""
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ValueError(f"vector has {len(vector)} dims, expected {self.dimensions}")
        rows = [(
            c.chunk_id, index_name, c.document_id, c.source, c.page, c.metadata["chapter"],
            c.metadata.get("chapter_title"), c.metadata.get("section") or None,
            c.metadata.get("article") or None, c.metadata.get("article_title") or None,
            c.metadata["char_start"], c.metadata["char_end"], c.text, c.metadata["content_hash"],
            model_name, _vec(v)) for c, v in zip(chunks, vectors)]
        with self.connect() as conn, conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO chunks (chunk_id, index_name, document_id, source, page, chapter,
                    chapter_title, section, article, article_title, char_start, char_end, text,
                    content_hash, embedding_model, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    index_name = EXCLUDED.index_name, document_id = EXCLUDED.document_id,
                    source = EXCLUDED.source, page = EXCLUDED.page, chapter = EXCLUDED.chapter,
                    chapter_title = EXCLUDED.chapter_title, section = EXCLUDED.section,
                    article = EXCLUDED.article, article_title = EXCLUDED.article_title,
                    char_start = EXCLUDED.char_start, char_end = EXCLUDED.char_end,
                    text = EXCLUDED.text, content_hash = EXCLUDED.content_hash,
                    embedding_model = EXCLUDED.embedding_model, embedding = EXCLUDED.embedding,
                    updated_at = now()""", rows)
        return len(rows)

    def prune(self, index_name: str, document_ids: list[str], keep_ids: list[str]) -> int:
        """Delete obsolete chunks of re-ingested documents (e.g. after a source change)."""
        with self.connect() as conn:
            cur = conn.execute("""
                DELETE FROM chunks
                WHERE index_name = %s AND document_id = ANY(%s) AND NOT (chunk_id = ANY(%s))""",
                (index_name, document_ids, keep_ids))
            return cur.rowcount

    def count(self, index_name: str | None = None) -> int:
        with self.connect() as conn:
            if index_name is None:
                return conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            return conn.execute("SELECT count(*) FROM chunks WHERE index_name = %s",
                                (index_name,)).fetchone()[0]

    def delete_index(self, index_name: str) -> int:
        with self.connect() as conn:
            return conn.execute("DELETE FROM chunks WHERE index_name = %s", (index_name,)).rowcount

    def search(self, index_name: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Exact (sequential-scan) cosine search; filters are applied in SQL before
        ranking, so a filtered query still returns up to top_k matching chunks."""
        if len(query_vector) != self.dimensions:
            raise ValueError("query vector dimension mismatch")
        where = ["index_name = %s"]
        params: list[Any] = [index_name]
        for key, value in (filters or {}).items():
            if key not in FILTERABLE_FIELDS:
                raise ValueError(f"filter field {key!r} not allowed")
            where.append(f"{key} = %s")  # key comes from the allow-list, value is a parameter
            params.append(value)
        vec = _vec(query_vector)
        sql = f"""
            SELECT {", ".join(_RESULT_COLUMNS)}, 1 - (embedding <=> %s::vector) AS score
            FROM chunks WHERE {" AND ".join(where)}
            ORDER BY embedding <=> %s::vector, chunk_id
            LIMIT %s"""
        with self.connect() as conn:
            rows = conn.execute(sql, [vec, *params, vec, top_k]).fetchall()
        return [dict(zip((*_RESULT_COLUMNS, "score"), row)) for row in rows]
