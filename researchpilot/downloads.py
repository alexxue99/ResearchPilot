"""Bounded public HTTPS paper retrieval, with DNS pinning and redirect validation."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class PaperDownloader:
    """Downloads only configured public repositories; does not bypass access controls."""

    def __init__(self, root: str | Path, allowed_hosts: set[str] | None = None,
                 max_bytes: int = 25_000_000, timeout: float = 30):
        self.root = Path(root)
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else {
            host.strip().lower() for host in os.environ.get("RESEARCHPILOT_PAPER_HOSTS",
                "arxiv.org,export.arxiv.org,pmc.ncbi.nlm.nih.gov").split(",") if host.strip()}
        if max_bytes < 1 or timeout <= 0:
            raise ValueError("download limits must be positive")
        self.max_bytes, self.timeout = max_bytes, timeout

    def _target(self, url: str):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.hostname.lower() not in self.allowed_hosts):
            raise ValueError("paper URL must use HTTPS on an allowed repository host")
        addresses = [item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)]
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("paper URL resolves to a non-public address")
        return parsed, addresses[0]

    def download(self, url: str) -> Path:
        original = url
        deadline = time.monotonic() + self.timeout
        for _ in range(4):
            parsed, address = self._target(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("paper download exceeded deadline")
            connection = _PinnedHTTPSConnection(parsed.hostname, address, remaining)
            try:
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                connection.request("GET", path, headers={"Accept": "application/pdf",
                    "User-Agent": "ResearchPilot/0.2", "Accept-Encoding": "identity"})
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("paper redirect has no location")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise ValueError(f"paper download returned HTTP {response.status}")
                length = response.getheader("Content-Length")
                if length and int(length) > self.max_bytes:
                    raise ValueError("paper exceeds download size limit")
                body = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("paper download exceeded deadline")
                    if connection.sock:
                        connection.sock.settimeout(remaining)
                    part = response.read1(min(65536, self.max_bytes + 1 - len(body)))
                    if not part:
                        break
                    body.extend(part)
                    if len(body) > self.max_bytes:
                        raise ValueError("paper exceeds download size limit")
                if not body.startswith(b"%PDF-"):
                    raise ValueError("repository did not return a PDF")
                digest = hashlib.sha256(body).hexdigest()
                self.root.mkdir(parents=True, exist_ok=True)
                target = self.root / f"{digest}.pdf"
                if not target.exists():
                    with target.open("xb") as handle:
                        handle.write(body)
                receipt = self.root / f"{digest}.provenance.json"
                if not receipt.exists():
                    with receipt.open("x", encoding="utf-8") as handle:
                        json.dump({"requested_url": original, "resolved_url": url,
                                   "sha256": digest, "bytes": len(body)}, handle, indent=2)
                return target
            finally:
                connection.close()
        raise ValueError("paper exceeded redirect limit")
