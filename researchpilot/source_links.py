"""Public links for papers with a verified arXiv identifier."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import Source

ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})(?:v\d+)?", re.IGNORECASE)


def arxiv_id_from_link(link: str) -> str:
    parsed = urlparse(link.strip())
    if (parsed.scheme != "https" or parsed.hostname not in {"arxiv.org", "www.arxiv.org"}
            or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment):
        raise ValueError("Enter an HTTPS arXiv abstract or PDF link")
    match = re.fullmatch(r"/(?:abs|pdf)/(.+?)(?:\.pdf)?/?", parsed.path)
    if not match or not ARXIV_ID.fullmatch(match.group(1)):
        raise ValueError("Enter a valid arXiv abstract or PDF link")
    return match.group(1)


def arxiv_url(source: Source) -> str | None:
    identifier = source.arxiv_id or (source.id[6:] if source.id.startswith("arxiv:") else "")
    if not identifier and source.doi and source.doi.lower().startswith("10.48550/arxiv."):
        identifier = source.doi[len("10.48550/arxiv."):]
    if not identifier and source.url:
        parsed = urlparse(source.url)
        if parsed.scheme == "https" and parsed.hostname in {"arxiv.org", "www.arxiv.org"}:
            identifier = re.sub(r"^/(?:abs|pdf)/", "", parsed.path).removesuffix(".pdf")
    if identifier and ARXIV_ID.fullmatch(identifier):
        return f"https://arxiv.org/abs/{identifier}"
    return None


def source_url(source: Source) -> str | None:
    """Link retrieved web sources as well as papers, without accepting executable URLs."""
    try:
        arxiv = arxiv_url(source)
        if arxiv:
            return arxiv
        if not source.url:
            return None
        parsed = urlparse(source.url.strip())
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return source.url.strip()
    except ValueError:
        pass
    return None
