"""Real local embedding model via Sentence Transformers (no API key, cost 0)."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Sentence Transformers adapter. Queries and documents are encoded the same way
    (this model has no query/document prompts) and L2-normalized, so cosine
    similarity in pgvector is well defined. Errors propagate: no mock fallback."""

    def __init__(self, model_name: str, batch_size: int = 32) -> None:
        if not model_name:
            raise ValueError("EMBEDDING_MODEL is empty; set it in starter/.env")
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.batch_size = batch_size
        self.model = SentenceTransformer(model_name, device="cpu")
        self.dimensions = int(self.model.get_embedding_dimension())
        self.max_seq_length = int(self.model.max_seq_length)
        self.revision = self._revision()

    def _revision(self) -> str:
        try:
            from huggingface_hub import snapshot_download
            return Path(snapshot_download(self.model_name, local_files_only=True)).name
        except Exception:  # model loaded from a local path or unknown cache layout
            return "unknown"

    def token_counts(self, texts: list[str]) -> list[int]:
        tokenizer = self.model.tokenizer
        return [len(ids) for ids in tokenizer(texts, add_special_tokens=True)["input_ids"]]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.model.encode(texts, batch_size=self.batch_size,
                                    normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()

    def embed_query(self, question: str) -> list[float]:
        return self.embed_documents([question])[0]
