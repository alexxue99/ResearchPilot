from __future__ import annotations

import json
import re
import hashlib
import urllib.parse
import urllib.request
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import Source


def has_article_title(source: Source) -> bool:
    title = source.title.strip()
    return bool(title and title != source.id and title.lower() != "untitled"
                and not re.match(r"^(?:https?://|doi:|arxiv:|10\.\d{4,9}/)", title, re.I))


class CrossrefClient:
    """Structured scholarly search with a disk cache and persistent DOI identifiers."""

    def __init__(self, cache_dir: str | Path = ".researchpilot/cache/crossref", timeout: float = 12,
                 ttl_seconds: int = 86400, version: str = "v1") -> None:
        self.cache_dir, self.timeout = Path(cache_dir), timeout
        self.ttl_seconds, self.version = ttl_seconds, version
        self.last_cache_hit = False
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def lookup_url(doi: str) -> str:
        return "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")

    def lookup(self, doi: str) -> Source | None:
        """Resolve an exact DOI, rather than guessing from a search result."""
        cache = self.cache_dir / (hashlib.sha256(f"doi|{doi.lower()}".encode()).hexdigest() + ".json")
        self.last_cache_hit = cache.exists() and time.time() - cache.stat().st_mtime < self.ttl_seconds
        if self.last_cache_hit:
            data = json.loads(cache.read_text(encoding="utf-8"))
        else:
            request = urllib.request.Request(
                self.lookup_url(doi),
                headers={"User-Agent": "ResearchPilot/0.2"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
            cache.write_text(json.dumps(data), encoding="utf-8")
        item = data.get("message", {})
        title = (item.get("title") or [""])[0]
        if item.get("DOI", "").lower() != doi.lower() or not title.strip():
            return None
        return Source(f"doi:{doi.lower()}", title, doi=doi, url=item.get("URL"), verified=True)

    def search(self, query: str, limit: int = 10, year_from: int | None = None,
               author: str | None = None) -> list[Source]:
        cache_key = hashlib.sha256(f"{self.version}|{query}|{limit}|{year_from}|{author}".encode()).hexdigest()
        cache = self.cache_dir / f"{cache_key}.json"
        self.last_cache_hit = cache.exists() and time.time() - cache.stat().st_mtime < self.ttl_seconds
        if self.last_cache_hit:
            data = json.loads(cache.read_text(encoding="utf-8"))
        else:
            params = {"query": query, "rows": str(limit), "select": "DOI,title,author,abstract,published,URL"}
            if author:
                params["query.author"] = author
            if year_from:
                params["filter"] = f"from-pub-date:{year_from}-01-01"
            request = urllib.request.Request("https://api.crossref.org/works?" + urllib.parse.urlencode(params),
                                             headers={"User-Agent": "ResearchPilot/0.1 (mailto:researchpilot@example.invalid)"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
            cache.write_text(json.dumps(data), encoding="utf-8")
        results = []
        for item in data.get("message", {}).get("items", []):
            title = (item.get("title") or ["Untitled"])[0]
            authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in item.get("author", [])]
            parts = (item.get("published", {}).get("date-parts") or [[None]])[0]
            doi = item.get("DOI")
            results.append(Source(id=f"doi:{doi}" if doi else item.get("URL", title), title=title,
                                  authors=authors, abstract=item.get("abstract", ""), year=parts[0],
                                  doi=doi, url=item.get("URL"), verified=bool(doi)))
        return results


class ArxivClient:
    """Search arXiv Atom metadata with an expiring, versioned disk cache."""

    def __init__(self, cache_dir: str | Path, timeout: float = 12,
                 ttl_seconds: int = 86400, version: str = "v1"):
        self.cache_dir, self.timeout = Path(cache_dir), timeout
        self.ttl_seconds, self.version = ttl_seconds, version
        self.last_cache_hit = False
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def lookup_url(arxiv_id: str) -> str:
        return "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": arxiv_id})

    def lookup(self, arxiv_id: str) -> Source | None:
        """Fetch one arXiv record so a linked PDF has its real title and authors."""
        cache = self.cache_dir / (hashlib.sha256(f"id|{arxiv_id}".encode()).hexdigest() + ".xml")
        self.last_cache_hit = cache.exists() and time.time() - cache.stat().st_mtime < self.ttl_seconds
        if self.last_cache_hit:
            document = cache.read_bytes()
        else:
            request = urllib.request.Request(
                self.lookup_url(arxiv_id),
                headers={"User-Agent": "ResearchPilot/0.2 (research demo)"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                document = response.read(2_000_001)
            if len(document) > 2_000_000:
                raise ValueError("arXiv response exceeds metadata limit")
            ET.fromstring(document)
            cache.write_bytes(document)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entry = ET.fromstring(document).find("a:entry", ns)
        if entry is None:
            return None
        identifier = entry.findtext("a:id", default="", namespaces=ns).split("/abs/", 1)[-1].rstrip("/")
        if (identifier != arxiv_id and
                (re.search(r"v\d+$", arxiv_id) or re.sub(r"v\d+$", "", identifier) != arxiv_id)):
            return None
        title = " ".join(entry.findtext("a:title", default="", namespaces=ns).split())
        if not title:
            return None
        authors = [" ".join(a.findtext("a:name", default="", namespaces=ns).split())
                   for a in entry.findall("a:author", ns)]
        abstract = " ".join(entry.findtext("a:summary", default="", namespaces=ns).split())
        published = entry.findtext("a:published", default="", namespaces=ns)
        year = int(published[:4]) if published[:4].isdigit() else None
        return Source(f"arxiv:{arxiv_id}", title, authors, abstract, year,
                      arxiv_id=arxiv_id, url=f"https://arxiv.org/pdf/{arxiv_id}", verified=True)

    def search(self, query: str, limit: int = 10) -> list[Source]:
        limit = max(1, min(limit, 20))
        key = hashlib.sha256(f"{self.version}|{query}|{limit}".encode()).hexdigest()
        cache = self.cache_dir / f"{key}.xml"
        self.last_cache_hit = cache.exists() and time.time() - cache.stat().st_mtime < self.ttl_seconds
        if self.last_cache_hit:
            document = cache.read_bytes()
        else:
            params = urllib.parse.urlencode({"search_query": "all:" + query,
                                             "start": 0, "max_results": limit})
            request = urllib.request.Request("https://export.arxiv.org/api/query?" + params,
                headers={"User-Agent": "ResearchPilot/0.2 (research demo)"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                document = response.read(2_000_001)
            if len(document) > 2_000_000:
                raise ValueError("arXiv response exceeds metadata limit")
            ET.fromstring(document)  # Validate before caching.
            cache.write_bytes(document)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in ET.fromstring(document).findall("a:entry", ns):
            identifier = entry.findtext("a:id", default="", namespaces=ns).split("/abs/", 1)[-1].rstrip("/")
            if not identifier:
                continue
            title = " ".join(entry.findtext("a:title", default="Untitled", namespaces=ns).split())
            abstract = " ".join(entry.findtext("a:summary", default="", namespaces=ns).split())
            authors = [" ".join(a.findtext("a:name", default="", namespaces=ns).split())
                       for a in entry.findall("a:author", ns)]
            published = entry.findtext("a:published", default="", namespaces=ns)
            year = int(published[:4]) if published[:4].isdigit() else None
            results.append(Source(f"arxiv:{identifier}", title, authors, abstract, year,
                                  arxiv_id=identifier, url=f"https://arxiv.org/pdf/{identifier}", verified=True))
        return results


