from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass


TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")


@dataclass(slots=True)
class Chunk:
    id: str
    source_id: str
    title: str
    section: str
    text: str
    page: int | None = None
    url: str | None = None


def semantic_chunks(source_id: str, title: str, text: str, url: str | None = None,
                    max_chars: int = 3500) -> list[Chunk]:
    """Section/paragraph-aware chunking, with size splitting only as a fallback."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    sections = re.split(r"\n(?=(?:#{1,3}\s+|(?:ABSTRACT|INTRODUCTION|METHODS?|RESULTS?|CONCLUSION|REFERENCES|THEOREM|LEMMA|PROPOSITION|COROLLARY|PROOF|DEFINITION)\b))", text, flags=re.I)
    chunks: list[Chunk] = []
    for section_index, section_text in enumerate(sections):
        lines = section_text.strip().splitlines()
        if not lines:
            continue
        is_heading = bool(re.match(r"^(?:#{1,3}\s|(?:abstract|introduction|methods?|results?|conclusion|references)\s*$)", lines[0], re.I))
        heading = lines[0].lstrip("# ") if is_heading or re.match(r"^(theorem|lemma|proposition|corollary|proof|definition)\b", lines[0], re.I) else f"section-{section_index}"
        paragraphs = re.split(r"\n\s*\n", "\n".join(lines[1:] if is_heading else lines))
        buffer = ""
        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                if buffer.strip():
                    chunks.append(Chunk(f"{source_id}:c{len(chunks)}", source_id, title, heading, buffer.strip(), url=url))
                    buffer = ""
                for offset in range(0, len(paragraph), max_chars):
                    chunks.append(Chunk(f"{source_id}:c{len(chunks)}", source_id, title, heading, paragraph[offset:offset + max_chars], url=url))
                continue
            if buffer and len(buffer) + len(paragraph) > max_chars:
                chunks.append(Chunk(f"{source_id}:c{len(chunks)}", source_id, title, heading, buffer.strip(), url=url))
                buffer = ""
            buffer += paragraph.strip() + "\n\n"
        if buffer.strip():
            chunks.append(Chunk(f"{source_id}:c{len(chunks)}", source_id, title, heading, buffer.strip(), url=url))
    return chunks


class LocalRetriever:
    def __init__(self) -> None:
        self.chunks: list[Chunk] = []

    def add(self, chunks: list[Chunk]) -> None:
        self.chunks.extend(chunks)

    @staticmethod
    def _terms(text: str) -> Counter[str]:
        return Counter(t.lower() for t in TOKEN.findall(text))

    def search(self, query: str, limit: int = 5) -> list[tuple[Chunk, float]]:
        q = self._terms(query)
        scored = []
        for chunk in self.chunks:
            d = self._terms(chunk.text + " " + chunk.section + " " + chunk.title)
            numerator = sum(q[t] * d[t] for t in q)
            denominator = math.sqrt(sum(v * v for v in q.values()) * sum(v * v for v in d.values()))
            scored.append((chunk, numerator / denominator if denominator else 0.0))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]
