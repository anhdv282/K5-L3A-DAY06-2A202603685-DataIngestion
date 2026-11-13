"""Fixed-size and structure-aware chunking. Chunks never cross a page boundary,
so every chunk keeps exactly one source page."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .ingestion import structure_at
from .models import Chunk, DocumentPage

# Sentence end followed by a capital letter / digit / quote, or a ";" ending a list item.
# (Not right after a clause number such as "1." at the start of a paragraph.)
_SENTENCE_END = re.compile(r"(?<!^\d\.)(?<!^\d\d\.)(?<=[.!?;])[\"”’)]?\s+(?=[A-ZÀ-Ỹ0-9“\"(])")
_TERMINAL = (".", "?", "!", ":", ";", "”", '"', ")")


def _make_chunk(index_name: str, page: DocumentPage, seq: int, start: int, end: int) -> Chunk:
    text = page.text[start:end]
    meta = {k: v for k, v in page.metadata.items() if k not in ("structure_spans", "article_offsets")}
    # Structural metadata is the one in effect where the chunk STARTS.
    meta.update(structure_at(page, start))
    meta.update({
        "index_name": index_name, "char_start": start, "char_end": end,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    })
    return Chunk(
        chunk_id=f"{index_name}:{page.document_id}:p{page.page:04d}:c{seq:03d}",
        document_id=page.document_id, source=page.source, page=page.page,
        text=text, metadata=meta,
    )


def fixed_size(page: DocumentPage, size: int, overlap: int, index_name: str = "fixed") -> list[Chunk]:
    """Overlapping character windows over one page (no boundary awareness, baseline)."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("require size > 0 and 0 <= overlap < size")
    chunks: list[Chunk] = []
    step = size - overlap
    start = 0
    while start < len(page.text):
        end = min(start + size, len(page.text))
        if page.text[start:end].strip():
            chunks.append(_make_chunk(index_name, page, len(chunks), start, end))
        if end == len(page.text):
            break
        start += step
    return chunks


@dataclass
class _Unit:
    start: int
    end: int
    kind: str  # "section" (article heading Điều n), "heading" (Chương/Mục/title), "body"


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for part in text.split("\n\n"):
        if part.strip():
            spans.append((pos, pos + len(part)))
        pos += len(part) + 2
    return spans


def _split_long(text: str, start: int, end: int, max_size: int) -> list[_Unit]:
    """Split an oversized paragraph at sentence/list boundaries; hard-cut only a
    single sentence that is itself longer than max_size."""
    cuts = [start] + [start + m.end() for m in _SENTENCE_END.finditer(text[start:end])] + [end]
    sentences = [(a, b) for a, b in zip(cuts, cuts[1:]) if text[a:b].strip()]
    units: list[_Unit] = []
    group_start = None
    group_end = None
    for a, b in sentences:
        b_trim = a + len(text[a:b].rstrip())
        if group_start is not None and b_trim - group_start > max_size:
            units.append(_Unit(group_start, group_end, "body"))
            group_start = None
        if group_start is None:
            group_start = a
        group_end = b_trim
        while group_end - group_start > max_size:  # one very long sentence: cut at a space
            cut = text.rfind(" ", group_start + 1, group_start + max_size)
            cut = cut if cut > group_start else group_start + max_size
            units.append(_Unit(group_start, cut, "body"))
            group_start = cut + 1 if text[cut:cut + 1] == " " else cut
    if group_start is not None and group_end > group_start:
        units.append(_Unit(group_start, group_end, "body"))
    return units


def by_paragraph(page: DocumentPage, max_size: int, index_name: str = "para") -> list[Chunk]:
    """Group whole paragraphs (khoản/điểm) up to `max_size` characters.

    - an article heading ("Điều n. ...") always starts a new chunk, so a chunk never
      mixes two articles; chapter/section headings just before it are kept with it;
    - other short heading-like paragraphs are carried forward to the next body text;
    - paragraphs longer than max_size are split at sentence boundaries.
    """
    article_offsets = set(page.metadata.get("article_offsets", []))
    units: list[_Unit] = []
    for start, end in _paragraph_spans(page.text):
        para = page.text[start:end]
        if start in article_offsets:
            units.append(_Unit(start, end, "section"))
        elif len(para) < 80 and not para.rstrip().endswith(_TERMINAL):
            units.append(_Unit(start, end, "heading"))
        elif end - start > max_size:
            units.extend(_split_long(page.text, start, end, max_size))
        else:
            units.append(_Unit(start, end, "body"))

    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    for unit in units:
        too_big = current and unit.end - current[0].start > max_size
        if current and (unit.kind == "section" or too_big):
            carry: list[_Unit] = []
            while current and current[-1].kind != "body":
                carry.insert(0, current.pop())
            if current:
                groups.append(current)
            current = carry
            # A run of short lines too long to prepend becomes its own chunk.
            if current and unit.end - current[0].start > max_size:
                groups.append(current)
                current = []
        current.append(unit)
    if current:
        if groups and all(u.kind != "body" for u in current):
            groups[-1].extend(current)  # trailing labels at page end
        else:
            groups.append(current)

    return [_make_chunk(index_name, page, i, g[0].start, g[-1].end) for i, g in enumerate(groups)]


def chunk_pages(pages: list[DocumentPage], index_name: str, spec: dict) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        if spec["chunker"] == "fixed_size":
            chunks.extend(fixed_size(page, spec["size"], spec["overlap"], index_name))
        elif spec["chunker"] == "by_paragraph":
            chunks.extend(by_paragraph(page, spec["max_size"], index_name))
        else:
            raise ValueError(f"unknown chunker {spec['chunker']}")
    return chunks
