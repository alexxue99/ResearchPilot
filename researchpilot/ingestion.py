from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .models import Source
from .rag import Chunk, semantic_chunks


@dataclass(slots=True)
class ParsedPaper:
    source: Source
    chunks: list[Chunk]
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)
    math_blocks: list[dict] = field(default_factory=list)


def extract_math_blocks(text: str, page: int | None = None) -> list[dict]:
    """Retain display equations and labeled statements, without asserting correctness."""
    patterns = {
        "equation": r"\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\begin\{(?:equation\*?|align\*?)\}[\s\S]+?\\end\{(?:equation\*?|align\*?)\}",
        "statement": r"(?im)^[# \t]*(?:theorem|lemma|proposition|corollary|definition|proof)\b[^\n]*(?:\n(?!\s*\n|[# \t]*(?:theorem|lemma|proof)\b)[^\n]+)*",
    }
    blocks = [(match.start(), {"kind": kind, "text": match.group().strip(), "page": page,
                               "start": match.start(), "end": match.end()})
              for kind, pattern in patterns.items() for match in re.finditer(pattern, text)]
    return [block for _, block in sorted(blocks, key=lambda pair: pair[0])]


class _TextHTMLParser(HTMLParser):
    BLOCKS = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "br"}
    def __init__(self) -> None:
        super().__init__(); self.parts: list[str] = []
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.BLOCKS: self.parts.append("\n")
    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCKS: self.parts.append("\n")
    def handle_data(self, data: str) -> None: self.parts.append(data)
    def text(self) -> str: return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


class PaperIngestor:
    """Ingest local papers while retaining source, page, section, and chunk IDs."""

    def __init__(self, max_pages: int | None = None):
        self.max_pages = max_pages

    def ingest(self, path: str | Path, source: Source) -> ParsedPaper:
        target = Path(path)
        if not target.is_file(): raise FileNotFoundError(target)
        suffix = target.suffix.lower()
        if suffix == ".pdf": return self._pdf(target, source)
        if suffix in (".html", ".htm"):
            parser = _TextHTMLParser(); parser.feed(target.read_text(encoding="utf-8", errors="replace"))
            text = parser.text()
        elif suffix in (".txt", ".md", ".rst"):
            text = target.read_text(encoding="utf-8", errors="replace")
        else: raise ValueError(f"unsupported paper format: {suffix or '<none>'}")
        chunks = semantic_chunks(source.id, source.title, text, source.url)
        return ParsedPaper(source, chunks, warnings=[] if chunks else ["document contained no retrievable text"],
                           math_blocks=extract_math_blocks(text))

    def _pdf(self, path: Path, source: Source) -> ParsedPaper:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Install ResearchPilot with the 'literature' extra to ingest PDF files") from exc
        reader = PdfReader(path)
        if self.max_pages is not None and len(reader.pages) > self.max_pages:
            raise ValueError(f"paper exceeds {self.max_pages} page limit")
        chunks: list[Chunk] = []; warnings: list[str] = []; math_blocks: list[dict] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text(extraction_mode="layout") or "") if "/Contents" in page else ""
            if not text.strip():
                warnings.append(f"page {page_number} contained no extractable text")
                continue
            page_chunks = semantic_chunks(source.id, source.title, text, source.url)
            math_blocks.extend(extract_math_blocks(text, page_number))
            for chunk in page_chunks:
                chunk.id = f"{source.id}:p{page_number}:c{len(chunks)}"
                chunk.page = page_number
                chunks.append(chunk)
        if chunks:
            warnings.append("PDF layout extraction is heuristic; equations, columns, and scanned pages require human verification.")
        return ParsedPaper(source, chunks, len(reader.pages), warnings, math_blocks)
