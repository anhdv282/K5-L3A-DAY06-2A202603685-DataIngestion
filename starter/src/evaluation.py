"""Evidence-based retrieval scoring (Hit@1, Evidence Hit@3) with full traces.

A retrieved chunk is an evidence hit when it comes from a gold source AND a gold
page AND contains the whole gold evidence quote (after canonical normalization).
Any documented alternative evidence (question["alternatives"]) is accepted.
Human labels (coherence, grounding) are NOT inferred here.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_WS = re.compile(r"\s+")


def canonical(text: str) -> str:
    """Matching-only normalization: NFKC, straight quotes/dashes, casefold, 1 space."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()
    return _WS.sub(" ", text).strip()


def gold_evidence(question: dict[str, Any]) -> list[dict[str, Any]]:
    pages = question["gold_page"]
    golds = [{"source_id": question["gold_source_id"],
              "pages": pages if isinstance(pages, list) else [pages],
              "evidence_quote": question["evidence_quote"]}]
    for alt in question.get("alternatives", []):
        alt_pages = alt["gold_page"]
        golds.append({"source_id": alt["gold_source_id"],
                      "pages": alt_pages if isinstance(alt_pages, list) else [alt_pages],
                      "evidence_quote": alt["evidence_quote"]})
    return golds


def _match(hit: dict[str, Any], golds: list[dict[str, Any]]) -> tuple[bool, bool]:
    """(full evidence hit, on a gold source/page)."""
    on_page = False
    text = canonical(hit["text"])
    for gold in golds:
        if hit["document_id"] == gold["source_id"] and hit["page"] in gold["pages"]:
            on_page = True
            if canonical(gold["evidence_quote"]) in text:
                return True, True
    return False, on_page


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]],
                   strategy: str = "", filter_applied: dict | None = None) -> dict[str, Any]:
    golds = gold_evidence(question)
    ranked = []
    for rank, hit in enumerate(hits, start=1):
        is_hit, on_page = _match(hit, golds)
        ranked.append({**hit, "rank": rank, "evidence_hit": is_hit, "on_gold_page": on_page})
    first = next((h["rank"] for h in ranked if h["evidence_hit"]), None)
    qfilter = question.get("filter") or None
    compliant = None
    if qfilter:
        compliant = bool(ranked) and all(
            all(h.get(k) == v for k, v in qfilter.items()) for h in ranked)
    # Diagnostic only: evidence page retrieved but quote not fully inside one chunk.
    split_evidence = first is None and any(h["on_gold_page"] for h in ranked)
    return {
        "strategy": strategy, "id": question["id"], "split": question["split"],
        "type": question.get("type"), "question": question["question"],
        "expected_answer": question["expected_answer"], "gold": golds,
        "question_filter": qfilter, "filter_applied": filter_applied,
        "hit_at_1": first == 1, "hit_at_3": first is not None and first <= 3,
        "first_hit_rank": first, "filter_compliant": compliant,
        "gold_page_without_full_evidence": split_evidence,
        "hits": ranked,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 / Evidence Hit@3 / filter compliance by strategy and split."""
    out: dict[str, Any] = {}
    for trace in traces:
        for split in (trace["split"], "all"):
            bucket = out.setdefault(trace["strategy"], {}).setdefault(split, {
                "n": 0, "hit1": 0, "hit3": 0, "filter_questions": 0, "filter_compliant": 0,
                "gold_page_without_full_evidence": 0})
            bucket["n"] += 1
            bucket["hit1"] += trace["hit_at_1"]
            bucket["hit3"] += trace["hit_at_3"]
            bucket["gold_page_without_full_evidence"] += trace["gold_page_without_full_evidence"]
            if trace["question_filter"]:
                bucket["filter_questions"] += 1
                bucket["filter_compliant"] += bool(trace["filter_compliant"])
    for splits in out.values():
        for b in splits.values():
            b["hit1_str"] = f"{b['hit1']}/{b['n']}"
            b["hit3_str"] = f"{b['hit3']}/{b['n']}"
            b["filter_str"] = (f"{b['filter_compliant']}/{b['filter_questions']}"
                               if b["filter_questions"] else "N/A")
    return out
