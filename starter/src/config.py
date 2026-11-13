"""Experiment configuration: paths, the two chunk indexes and the three strategies.

Strategies A/B/C share corpus, embedding model, vector store, metric and benchmark.
A vs B changes only the chunking; B vs C changes only metadata filtering
(C reuses the B index).
"""
from pathlib import Path

STARTER_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = STARTER_DIR.parent

MANIFEST_PATH = ROOT_DIR / "data" / "manifest.csv"
BENCHMARK_PATH = ROOT_DIR / "benchmark" / "questions.jsonl"
RESULTS_DIR = ROOT_DIR / "results"
TRACES_DIR = RESULTS_DIR / "traces"
AUDIT_DIR = RESULTS_DIR / "audit"
LOG_DIR = RESULTS_DIR / "logs"

# Chunk indexes stored in pgvector (column `index_name`).
INDEXES: dict[str, dict] = {
    # 450 chars of Vietnamese legal text ~ 110 tokens: fits the model's 128-token window
    # (at 500 chars, 198/442 fixed chunks exceeded it and were truncated).
    "fixed450": {"chunker": "fixed_size", "size": 450, "overlap": 50},
    "para450": {"chunker": "by_paragraph", "max_size": 450},
}

# Retrieval strategies evaluated in the benchmark.
STRATEGIES: dict[str, dict] = {
    "A": {"index": "fixed450", "use_filter": False,
          "description": "Fixed 450-char windows, 50 overlap, per page; no filter"},
    "B": {"index": "para450", "use_filter": False,
          "description": "Paragraph/heading-aware chunks <=450 chars (never mixing two articles), per page; no filter"},
    "C": {"index": "para450", "use_filter": True,
          "description": "Same chunks as B; apply `chapter` filter only when the question provides one"},
}

TOP_K = 3
# Only these metadata fields may be used as filters (allow-list for SQL).
FILTERABLE_FIELDS = {"chapter"}
MIN_CONTENT_PAGES = 30
# A page whose normalized text is shorter than this is logged as (near) empty.
MIN_PAGE_CHARS = 200
