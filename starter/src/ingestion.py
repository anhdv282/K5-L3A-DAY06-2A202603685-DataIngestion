"""Read and normalize a Vietnamese legal PDF while keeping page-level traceability.

Structure of the corpus (Bộ luật Lao động 2019):
    Chương <La Mã> / TÊN CHƯƠNG  ->  Mục <n>. Tên mục  ->  Điều <n>. Tên điều
    -> khoản "1." -> điểm "a)".
pypdf returns one line per visual line, so paragraphs are rebuilt from these
structural markers. Chapter/section/article metadata comes from the headings in
the text itself (never guessed) and is tracked across page boundaries.
"""
from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

from .config import MIN_PAGE_CHARS
from .models import DocumentPage

_WS = re.compile(r"[ \t   ]+")
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍﻿"), None)
_DECORATION = re.compile(r"^[-_=.*\s]{3,}$")          # "--------", "______"
_CHAPTER = re.compile(r"^Chương ([IVXLC]+)$")
_SECTION = re.compile(r"^Mục (\d+)\.?\s*(.*)$")
_ARTICLE = re.compile(r"^Điều (\d+)\.\s*(.*)$")
_CLAUSE = re.compile(r"^\d+\.(\s|$)")               # khoản: "1. ..." (or "1." alone on a line)
_POINT = re.compile(r"^[a-zđ]\)(\s|$)")             # điểm:  "a) ..."
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


@dataclass
class ManifestEntry:
    source_id: str
    title: str
    url: str
    local_path: Path
    sha256: str
    pages: list[int]
    license: str


@dataclass
class IngestReport:
    """Per-run log of what was read, removed or flagged."""
    pages_read: int = 0
    empty_pages: list[str] = field(default_factory=list)
    removed_lines: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def parse_page_range(spec: str) -> list[int]:
    """'1-46' or '1-30,32' -> list of page numbers."""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(x) for x in part.split("-"))
            pages.extend(range(start, end + 1))
        elif part:
            pages.append(int(part))
    return pages


def read_manifest(path: Path, root: Path) -> list[ManifestEntry]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [ManifestEntry(
        source_id=row["source_id"], title=row["title"], url=row["source_path_or_url"],
        local_path=root / row["local_path"], sha256=row["sha256"],
        pages=parse_page_range(row["counted_pages"]), license=row["license_or_permission"],
    ) for row in rows]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def roman_to_int(numeral: str) -> int:
    total = 0
    for i, ch in enumerate(numeral):
        value = _ROMAN[ch]
        total += -value if i + 1 < len(numeral) and _ROMAN[numeral[i + 1]] > value else value
    return total


def normalize_text(text: str) -> str:
    """Unicode NFKC (composed Vietnamese diacritics, compatibility chars), drop
    invisible chars, collapse horizontal whitespace. Newlines are kept."""
    text = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _is_upper_title(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


@dataclass
class _State:
    """Structural position carried from page to page."""
    chapter: int = 0
    chapter_title: str = "Phần mở đầu (quốc hiệu, số hiệu, căn cứ ban hành)"
    section: str = ""
    article: int = 0
    article_title: str = ""
    expecting_chapter_title: bool = False

    def snapshot(self) -> dict:
        return {"chapter": self.chapter, "chapter_title": self.chapter_title,
                "section": self.section, "article": self.article,
                "article_title": self.article_title}


def build_page(raw: str, state: _State) -> tuple[str, list[str], list[tuple[int, dict]], set[int]]:
    """Rebuild paragraphs for one page and record structure changes.

    Returns (text, removed_lines, structure_spans, article_heading_offsets).
    `structure_spans` is [(char_offset, metadata_in_effect_from_there)], starting
    with the state carried over from the previous page at offset 0.
    """
    removed: list[str] = []
    paragraphs: list[str] = []
    spans: list[tuple[int, dict]] = [(0, state.snapshot())]
    article_offsets: set[int] = set()
    current: list[str] = []
    offset = 0

    def flush() -> None:
        nonlocal offset
        if current:
            paragraph = " ".join(current).strip()
            paragraphs.append(paragraph)
            offset += len(paragraph) + 2
            current.clear()

    for raw_line in normalize_text(raw).split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if _DECORATION.match(line):
            removed.append(line)
            continue

        chapter = _CHAPTER.match(line)
        section = _SECTION.match(line)
        article = _ARTICLE.match(line)
        if chapter:
            flush()
            state.chapter = roman_to_int(chapter.group(1))
            state.chapter_title, state.section = "", ""
            state.article, state.article_title = 0, ""
            state.expecting_chapter_title = True
            spans.append((offset, state.snapshot()))
            current.append(line)
            flush()
            continue
        if state.expecting_chapter_title and _is_upper_title(line):
            # Chapter titles may wrap over several upper-case lines.
            current.append(line)
            state.chapter_title = (state.chapter_title + " " + line).strip()
            spans[-1] = (spans[-1][0], state.snapshot())
            continue
        if state.expecting_chapter_title:
            state.expecting_chapter_title = False
            flush()
        if section:
            flush()
            state.section = f"Mục {section.group(1)}. {section.group(2)}".strip()
            spans.append((offset, state.snapshot()))
            current.append(line)
            flush()
            continue
        if article:
            flush()
            state.article = int(article.group(1))
            state.article_title = article.group(2).strip()
            spans.append((offset, state.snapshot()))
            article_offsets.add(offset)
            current.append(line)
            flush()
            continue
        if _CLAUSE.match(line) or _POINT.match(line):
            flush()
        current.append(line)
    flush()
    return "\n\n".join(paragraphs), removed, spans, article_offsets


def load_pdf(path: Path, document_id: str, source: str, pages: list[int] | None = None,
             metadata: dict | None = None, report: IngestReport | None = None) -> list[DocumentPage]:
    """Extract each requested physical page (1-based), keeping page number and source.

    All pages are parsed in order (so chapter/article state is correct even for a
    page subset); only the requested pages are returned. Pages with little text are
    recorded in `report.empty_pages`; OCR is not needed (Word-generated PDF).
    """
    report = report or IngestReport()
    reader = PdfReader(path)
    wanted = set(pages or range(1, len(reader.pages) + 1))
    for page_no in sorted(wanted):
        if page_no < 1 or page_no > len(reader.pages):
            report.warnings.append(f"{document_id}: page {page_no} out of range")
    state = _State()
    out: list[DocumentPage] = []
    for page_no in range(1, len(reader.pages) + 1):
        raw = reader.pages[page_no - 1].extract_text() or ""
        text, removed, spans, article_offsets = build_page(raw, state)
        if page_no not in wanted:
            continue
        page_id = f"{document_id}:p{page_no}"
        report.pages_read += 1
        report.removed_lines[page_id] = removed
        if len(text) < MIN_PAGE_CHARS:
            report.empty_pages.append(f"{page_id} ({len(text)} chars)")
        out.append(DocumentPage(
            document_id=document_id, source=source, page=page_no, text=text,
            metadata={**(metadata or {}), "page_id": page_id, "structure_spans": spans,
                      "article_offsets": sorted(article_offsets)},
        ))
    return out


def structure_at(page: DocumentPage, offset: int) -> dict:
    """Chapter/section/article metadata in effect at character `offset`."""
    current = page.metadata["structure_spans"][0][1]
    for start, meta in page.metadata["structure_spans"]:
        if start <= offset:
            current = meta
    return current
