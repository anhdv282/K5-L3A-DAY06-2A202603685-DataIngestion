"""Day 06 pipeline entry point. Run from `starter/`:

    python -m src.run ingest            # read, normalize, chunk, embed, store (A/B/C indexes)
    python -m src.run reingest-test     # prove re-ingest is idempotent and prunes changed docs
    python -m src.run verify-benchmark  # every evidence quote must exist on its gold page
    python -m src.run retrieve --split dev
    python -m src.run retrieve --split test   # only after the strategy is chosen on dev
    python -m src.run audit-sheets      # CSV sheets for manual coherence/metadata/grounding labels
    python -m src.run audit-summary     # aggregate the filled-in manual labels
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import config
from .chunking import chunk_pages
from .evaluation import canonical, evaluate_query, summarize
from .ingestion import IngestReport, load_pdf, read_manifest, sha256_file
from .models import DocumentPage

log = logging.getLogger("day06")


# ---------------------------------------------------------------- helpers
def _setup(command: str) -> None:
    load_dotenv(config.STARTER_DIR / ".env")
    for d in (config.RESULTS_DIR, config.TRACES_DIR, config.AUDIT_DIR, config.LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(config.LOG_DIR / f"{command}.log", mode="w", encoding="utf-8")])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _embedder():
    from .embeddings import MyEmbedder
    return MyEmbedder(os.environ.get("EMBEDDING_MODEL", ""))


def _store(dimensions: int):
    from .store import PgVectorStore
    store = PgVectorStore(os.environ.get("DATABASE_URL", ""), dimensions)
    store.create_schema()
    return store


def _environment(embedder=None) -> dict[str, Any]:
    import importlib.metadata as md
    env = {"run_at_utc": _now(), "python": platform.python_version(), "platform": platform.platform()}
    for pkg in ("pypdf", "psycopg", "sentence-transformers", "torch", "transformers"):
        try:
            env[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            env[pkg] = None
    if embedder is not None:
        env.update({"embedding_model": embedder.model_name, "model_revision": embedder.revision,
                    "dimensions": embedder.dimensions, "max_seq_length": embedder.max_seq_length,
                    "provider": "local sentence-transformers (CPU)", "cost_usd": 0})
    return env


def load_corpus(report: IngestReport) -> list[DocumentPage]:
    pages: list[DocumentPage] = []
    for entry in read_manifest(config.MANIFEST_PATH, config.ROOT_DIR):
        if not entry.local_path.exists():
            raise FileNotFoundError(f"{entry.local_path} missing: run python scripts/fetch_corpus.py")
        digest = sha256_file(entry.local_path)
        if digest != entry.sha256:
            raise RuntimeError(f"{entry.local_path}: sha256 {digest} != manifest {entry.sha256}")
        pages.extend(load_pdf(entry.local_path, entry.source_id, entry.url, entry.pages,
                              {"source_title": entry.title},
                              report))
    return pages


def _length_stats(values: list[int]) -> dict[str, float]:
    q = statistics.quantiles(values, n=4)
    return {"min": min(values), "p25": q[0], "median": statistics.median(values), "p75": q[2],
            "max": max(values), "mean": round(statistics.mean(values), 1)}


def ingest_index(store, embedder, index_name: str, spec: dict, pages: list[DocumentPage]) -> dict:
    t0 = time.perf_counter()
    chunks = chunk_pages(pages, index_name, spec)
    t_chunk = time.perf_counter() - t0
    existing = store.existing_hashes(index_name, embedder.model_name)
    todo = [c for c in chunks if existing.get(c.chunk_id) != c.metadata["content_hash"]]
    t1 = time.perf_counter()
    vectors = embedder.embed_documents([c.text for c in todo])
    t_embed = time.perf_counter() - t1
    t2 = time.perf_counter()
    store.upsert(index_name, todo, vectors, embedder.model_name)
    pruned = store.prune(index_name, sorted({c.document_id for c in chunks}),
                         [c.chunk_id for c in chunks])
    t_store = time.perf_counter() - t2
    lengths = [len(c.text) for c in chunks]
    tokens = embedder.token_counts([c.text for c in chunks])
    return {
        "index": index_name, "spec": spec, "chunks": len(chunks), "embedded_now": len(todo),
        "unchanged_skipped": len(chunks) - len(todo), "pruned": pruned,
        "rows_in_store": store.count(index_name),
        "char_length": _length_stats(lengths), "total_chars": sum(lengths),
        "token_length": _length_stats(tokens), "total_tokens": sum(tokens),
        "chunks_over_max_seq_length": sum(t > embedder.max_seq_length for t in tokens),
        "seconds": {"chunk": round(t_chunk, 3), "embed": round(t_embed, 3),
                    "store": round(t_store, 3), "total": round(time.perf_counter() - t0, 3)},
        "cost_usd": 0,
    }


# ---------------------------------------------------------------- commands
def cmd_ingest(args) -> None:
    report = IngestReport()
    t0 = time.perf_counter()
    pages = load_corpus(report)
    read_seconds = time.perf_counter() - t0
    content_pages = [p for p in pages if len(p.text) >= config.MIN_PAGE_CHARS]
    log.info("read %d pages (%d with >= %d chars) in %.1fs", len(pages), len(content_pages),
             config.MIN_PAGE_CHARS, read_seconds)
    if len(content_pages) < config.MIN_CONTENT_PAGES:
        raise SystemExit(f"only {len(content_pages)} content pages; need {config.MIN_CONTENT_PAGES}")

    _write_jsonl(config.RESULTS_DIR / "pages_normalized.jsonl", [{
        "page_id": p.metadata["page_id"], "document_id": p.document_id, "page": p.page,
        "structure_spans": p.metadata["structure_spans"],
        "article_offsets": p.metadata["article_offsets"], "chars": len(p.text), "text": p.text,
    } for p in pages])
    per_doc: dict[str, dict] = {}
    for p in pages:
        d = per_doc.setdefault(p.document_id, {"pages": 0, "chars": 0, "page_ids": []})
        d["pages"] += 1
        d["chars"] += len(p.text)
        d["page_ids"].append(p.page)
    _write_json(config.RESULTS_DIR / "corpus_stats.json", {
        "pages_total": len(pages), "content_pages": len(content_pages),
        "chars_total": sum(len(p.text) for p in pages), "per_document": per_doc,
        "read_seconds": round(read_seconds, 2)})
    _write_json(config.RESULTS_DIR / "ingest_report.json", {
        "pages_read": report.pages_read, "empty_or_short_pages": report.empty_pages,
        "warnings": report.warnings,
        "removed_header_footer_lines": sum(len(v) for v in report.removed_lines.values()),
        "removed_lines_by_page": report.removed_lines})
    for page_id in report.empty_pages:
        log.warning("short/empty page: %s", page_id)

    embedder = _embedder()
    store = _store(embedder.dimensions)
    stats = {"environment": _environment(embedder), "indexes": {}}
    for name, spec in config.INDEXES.items():
        result = ingest_index(store, embedder, name, spec, pages)
        stats["indexes"][name] = result
        log.info("%s: %d chunks, embedded %d, pruned %d, rows %d, %.1fs", name, result["chunks"],
                 result["embedded_now"], result["pruned"], result["rows_in_store"],
                 result["seconds"]["total"])
    _write_json(config.RESULTS_DIR / "ingest_stats.json", stats)

    # Second pass with the same corpus version: must embed nothing and add no rows.
    check = {"run_at_utc": _now(), "indexes": {}}
    for name, spec in config.INDEXES.items():
        before = store.count(name)
        again = ingest_index(store, embedder, name, spec, pages)
        check["indexes"][name] = {"rows_before": before, "rows_after": store.count(name),
                                  "embedded_on_second_pass": again["embedded_now"],
                                  "pruned_on_second_pass": again["pruned"]}
    check["ok"] = all(v["rows_before"] == v["rows_after"] and v["embedded_on_second_pass"] == 0
                      for v in check["indexes"].values())
    _write_json(config.RESULTS_DIR / "reingest_check.json", check)
    log.info("re-ingest same version: %s", "OK (no duplicates)" if check["ok"] else "FAILED")


def cmd_reingest_test(args) -> None:
    """Simulate a changed source document on a scratch index and record the effect."""
    report = IngestReport()
    pages = load_corpus(report)[1:4]  # pages 2-4
    embedder = _embedder()
    store = _store(embedder.dimensions)
    name, spec = "_reingest_test", config.INDEXES["para450"]
    store.delete_index(name)
    first = ingest_index(store, embedder, name, spec, pages)
    changed = [DocumentPage(p.document_id, p.source, p.page, p.text, dict(p.metadata)) for p in pages]
    changed[1].text = changed[1].text[: len(changed[1].text) // 2]  # "new version" of page 2
    second = ingest_index(store, embedder, name, spec, changed)
    result = {
        "run_at_utc": _now(), "scenario": "pages 2-4 of blld-2019; the middle page truncated to half in v2",
        "v1": {k: first[k] for k in ("chunks", "embedded_now", "pruned", "rows_in_store")},
        "v2": {k: second[k] for k in ("chunks", "embedded_now", "unchanged_skipped", "pruned",
                                       "rows_in_store")},
    }
    result["ok"] = second["rows_in_store"] == second["chunks"]
    store.delete_index(name)
    _write_json(config.RESULTS_DIR / "reingest_changed_doc_test.json", result)
    log.info("changed-document test: %s", result)


def load_benchmark() -> list[dict]:
    questions = _read_jsonl(config.BENCHMARK_PATH)
    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate question ids")
    return questions


def cmd_verify_benchmark(args) -> None:
    pages = {(r["document_id"], r["page"]): r["text"]
             for r in _read_jsonl(config.RESULTS_DIR / "pages_normalized.jsonl")}
    questions = load_benchmark()
    problems, rows = [], []
    from .evaluation import gold_evidence
    for q in questions:
        for g in gold_evidence(q):
            found = [p for p in g["pages"]
                     if canonical(g["evidence_quote"]) in canonical(pages.get((g["source_id"], p), ""))]
            ok = len(found) == len(g["pages"])
            rows.append({"id": q["id"], "split": q["split"], "source": g["source_id"],
                         "pages": g["pages"], "found_on": found, "ok": ok})
            if not ok:
                problems.append(f"{q['id']}: evidence not found on {g['source_id']} pages {g['pages']} (found on {found})")
        if q.get("filter"):
            bad = set(q["filter"]) - config.FILTERABLE_FIELDS
            if bad:
                problems.append(f"{q['id']}: filter fields {bad} not allowed")
    splits = {s: sum(q["split"] == s for q in questions) for s in ("dev", "test")}
    filters = {s: sum(bool(q.get("filter")) and q["split"] == s for q in questions) for s in ("dev", "test")}
    summary = {"checked_at_utc": _now(), "questions": len(questions), "splits": splits,
               "filter_questions": filters, "problems": problems, "rows": rows,
               "ok": not problems and len(questions) >= 20 and splits["dev"] >= 15
               and splits["test"] >= 5 and sum(filters.values()) >= 3 and filters["test"] >= 1}
    _write_json(config.RESULTS_DIR / "benchmark_verification.json", summary)
    for p in problems:
        log.error(p)
    log.info("benchmark: %d questions %s, filters %s -> %s", len(questions), splits, filters,
             "OK" if summary["ok"] else "PROBLEMS")
    if not summary["ok"]:
        raise SystemExit(1)


def cmd_retrieve(args) -> None:
    questions = [q for q in load_benchmark() if args.split == "all" or q["split"] == args.split]
    embedder = _embedder()
    store = _store(embedder.dimensions)
    t0 = time.perf_counter()
    qvecs = {q["id"]: embedder.embed_query(q["question"]) for q in questions}
    embed_seconds = time.perf_counter() - t0
    run_info = {"split": args.split, "environment": _environment(embedder),
                "query_embed_seconds": round(embed_seconds, 3), "strategies": {}}
    for name, strat in config.STRATEGIES.items():
        traces, t1 = [], time.perf_counter()
        for q in questions:
            filt = q.get("filter") if strat["use_filter"] and q.get("filter") else None
            hits = store.search(strat["index"], qvecs[q["id"]], config.TOP_K, filt)
            traces.append(evaluate_query(q, hits, name, filt))
        run_info["strategies"][name] = {**strat, "search_seconds": round(time.perf_counter() - t1, 3)}
        _write_jsonl(config.TRACES_DIR / f"{name}_{args.split}.jsonl", traces)
    _write_json(config.RESULTS_DIR / f"retrieve_run_{args.split}.json", run_info)

    all_traces = []
    for path in sorted(config.TRACES_DIR.glob("*_*.jsonl")):
        if path.stem.split("_")[1] in ("dev", "test"):
            all_traces.extend(_read_jsonl(path))
    summary = summarize(all_traces)
    _write_json(config.RESULTS_DIR / "summary.json", summary)
    lines = ["| Strategy | Split | Hit@1 | Evidence Hit@3 | Filter compliant | Gold page but evidence split |",
             "|---|---|---|---|---|---|"]
    for name in sorted(summary):
        for split in ("dev", "test", "all"):
            if split in summary[name]:
                b = summary[name][split]
                lines.append(f"| {name} | {split} | {b['hit1_str']} | {b['hit3_str']} | {b['filter_str']} "
                             f"| {b['gold_page_without_full_evidence']} |")
    (config.RESULTS_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def _clip(text: str, n: int = 400) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def cmd_audit_sheets(args) -> None:
    """Create CSV sheets; the label columns are left empty for the human rater."""
    from .chunking import chunk_pages as _chunk
    pages_rows = sorted(_read_jsonl(config.RESULTS_DIR / "pages_normalized.jsonl"),
                        key=lambda r: (r["document_id"], r["page"]))
    pages = [DocumentPage(r["document_id"], "", r["page"], r["text"],
                          {"page_id": r["page_id"], "article_offsets": r["article_offsets"],
                           "structure_spans": [tuple(s) for s in r["structure_spans"]]})
             for r in pages_rows]
    # 10 positions fixed before looking at scores: evenly spaced pages, middle of the page text.
    picks = [pages[round(i * (len(pages) - 1) / 9)] for i in range(10)]
    with (config.AUDIT_DIR / "coherence.csv").open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(["position_id", "page_id", "offset", "text_at_position", "strategy", "chunk_id",
                    "chunk_text", "label_0_1_2", "reason"])
        for i, page in enumerate(picks, start=1):
            offset = len(page.text) // 2
            for strat in ("A", "B"):
                spec_name = config.STRATEGIES[strat]["index"]
                chunks = _chunk([page], spec_name, config.INDEXES[spec_name])
                covering = [c for c in chunks if c.metadata["char_start"] <= offset < c.metadata["char_end"]]
                chunk = covering[0]  # first chunk covering the position
                w.writerow([f"pos{i:02d}", page.metadata["page_id"], offset,
                            _clip(page.text[max(0, offset - 60): offset + 60], 140), strat,
                            chunk.chunk_id, chunk.text, "", ""])

    test = {s: _read_jsonl(config.TRACES_DIR / f"{s}_test.jsonl") for s in config.STRATEGIES
            if (config.TRACES_DIR / f"{s}_test.jsonl").exists()}
    if not test:
        log.warning("no test traces yet: metadata/grounding sheets skipped")
        return
    page_text = {(r["document_id"], r["page"]): canonical(r["text"]) for r in pages_rows}
    with (config.AUDIT_DIR / "metadata.csv").open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(["id", "strategy", "rank", "chunk_id", "document_id", "source", "page",
                    "chapter", "article", "article_title", "question_filter", "auto_filter_compliant",
                    "auto_text_found_on_page", "text_preview", "source_ok", "page_ok", "chapter_ok",
                    "article_ok", "notes"])
        for strat, traces in test.items():
            for t in traces:
                for hit in t["hits"]:
                    filt = t["question_filter"]
                    auto_f = "" if not filt else all(hit.get(k) == v for k, v in filt.items())
                    found = canonical(hit["text"]) in page_text.get((hit["document_id"], hit["page"]), "")
                    w.writerow([t["id"], strat, hit["rank"], hit["chunk_id"], hit["document_id"],
                                hit["source"], hit["page"], hit["chapter"], hit["article"],
                                hit["article_title"], json.dumps(filt) if filt else "", auto_f, found,
                                _clip(hit["text"], 200), "", "", "", "", ""])
    with (config.AUDIT_DIR / "grounding.csv").open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(["id", "strategy", "question", "question_filter", "expected_answer",
                    "evidence_quote", "gold_pages", "top1", "top2", "top3", "auto_evidence_hit3",
                    "score_0_1_2", "reason"])
        for strat, traces in test.items():
            for t in traces:
                tops = [f"[{h['document_id']} p{h['page']} {h['score']:.3f}] {h['text']}" for h in t["hits"]]
                tops += [""] * (3 - len(tops))
                w.writerow([t["id"], strat, t["question"], json.dumps(t["question_filter"]) if t["question_filter"] else "",
                            t["expected_answer"], t["gold"][0]["evidence_quote"],
                            t["gold"][0]["pages"], *tops, t["hit_at_3"], "", ""])
    log.info("audit sheets written to %s", config.AUDIT_DIR)


def cmd_audit_summary(args) -> None:
    out: dict[str, Any] = {}

    def rows(name: str) -> list[dict]:
        path = config.AUDIT_DIR / name
        if not path.exists():
            return []
        with path.open(encoding="utf-8", newline="") as h:
            return list(csv.DictReader(h))

    coh = rows("coherence.csv")
    for strat in ("A", "B"):
        labels = [int(r["label_0_1_2"]) for r in coh if r["strategy"] == strat and r["label_0_1_2"].strip()]
        out.setdefault(strat, {})["coherence"] = {
            "n": len(labels), "mean": round(sum(labels) / len(labels), 2) if labels else None,
            "counts": {k: labels.count(k) for k in (0, 1, 2)}}
    out.setdefault("C", {})["coherence"] = {**out["B"]["coherence"], "note": "C reuses B chunks"}

    meta = rows("metadata.csv")
    for strat in config.STRATEGIES:
        recs = [r for r in meta if r["strategy"] == strat]
        labelled = [r for r in recs if all(r[k].strip() for k in ("source_ok", "page_ok", "chapter_ok", "article_ok"))]
        good = [r for r in labelled if all(r[k].strip().lower() in ("1", "y", "yes", "true")
                                           for k in ("source_ok", "page_ok", "chapter_ok", "article_ok"))]
        out.setdefault(strat, {})["metadata"] = {"records": len(recs), "labelled": len(labelled),
                                                 "complete_and_correct": len(good)}

    grd = rows("grounding.csv")
    for strat in config.STRATEGIES:
        scores = [int(r["score_0_1_2"]) for r in grd if r["strategy"] == strat and r["score_0_1_2"].strip()]
        out.setdefault(strat, {})["grounding"] = {
            "n": len(scores), "counts": {k: scores.count(k) for k in (0, 1, 2)},
            "mean": round(sum(scores) / len(scores), 2) if scores else None}
    _write_json(config.RESULTS_DIR / "audit_summary.json", out)
    print(json.dumps(out, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest")
    sub.add_parser("reingest-test")
    sub.add_parser("verify-benchmark")
    r = sub.add_parser("retrieve")
    r.add_argument("--split", choices=["dev", "test", "all"], required=True)
    sub.add_parser("audit-sheets")
    sub.add_parser("audit-summary")
    args = parser.parse_args()
    _setup(args.command)
    {"ingest": cmd_ingest, "reingest-test": cmd_reingest_test,
     "verify-benchmark": cmd_verify_benchmark, "retrieve": cmd_retrieve,
     "audit-sheets": cmd_audit_sheets, "audit-summary": cmd_audit_summary}[args.command](args)


if __name__ == "__main__":
    main()
