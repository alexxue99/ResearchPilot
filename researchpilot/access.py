"""Optional bearer identities with physically separate per-subject workspaces."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path


def load_identity_records(identity_file: str):
    with Path(identity_file).open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("identity file exceeds size limit")
    entries = json.loads(raw.decode("utf-8"))
    if not isinstance(entries, list) or not 1 <= len(entries) <= 128:
        raise ValueError("identity file must contain 1 to 128 identity records")
    hashes = set()
    oidc_subjects = set()
    subjects = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) not in (
                {"subject", "role", "token_sha256"}, {"subject", "role", "oidc_subject"})
                or not isinstance(entry["subject"], str) or not entry["subject"].strip()
                or len(entry["subject"]) > 200 or entry["role"] not in ("reader", "researcher")):
            raise ValueError("invalid identity record; expected subject, role, and token_sha256 or oidc_subject")
        if "token_sha256" in entry:
            digest = entry["token_sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest in hashes:
                raise ValueError("identity token hashes must be valid and unique")
            hashes.add(digest)
        else:
            external = entry["oidc_subject"]
            if not isinstance(external, str) or not 1 <= len(external) <= 512 or external in oidc_subjects:
                raise ValueError("OIDC subjects must be valid and unique")
            oidc_subjects.add(external)
        subjects.add(entry["subject"])
    if len(subjects) > 32:
        raise ValueError("single-node multi-user mode supports at most 32 workspaces")
    return entries, subjects, oidc_subjects


def create_multiuser_app(root: Path, identity_file: str):
    from starlette.applications import Starlette
    from starlette.middleware.cors import CORSMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Mount
    from .api import create_app

    if os.environ.get("RESEARCHPILOT_EXECUTOR") != "docker":
        raise ValueError("multi-user mode requires RESEARCHPILOT_EXECUTOR=docker")
    entries, subjects, oidc_subjects = load_identity_records(identity_file)
    verifier = None
    if oidc_subjects:
        from .identity import OIDCVerifier
        verifier = OIDCVerifier.from_env()
    children = {}
    for subject in subjects:
        workspace = (root / "users" / hashlib.sha256(subject.encode()).hexdigest()).resolve()
        if root not in workspace.parents:
            raise ValueError("user workspace escapes configured root")
        children[subject] = create_app(workspace, _scoped=True)

    class Router:
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await send({"type": "websocket.close", "code": 1008})
                return
            if scope["path"] == "/health":
                await JSONResponse({"status": "ok", "version": "0.2.0", "multi_user": True})(scope, receive, send)
                return
            try:
                entries, current_subjects, current_oidc = await asyncio.to_thread(load_identity_records, identity_file)
                if not current_subjects.issubset(children) or (current_oidc and verifier is None):
                    raise ValueError("new workspace or verifier requires restart")
            except (OSError, ValueError, TypeError):
                await JSONResponse({"detail": "identity configuration unavailable"}, status_code=503)(scope, receive, send)
                return
            supplied = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
            identity = None
            if len(supplied) == 1 and supplied[0].startswith(b"Bearer ") and len(supplied[0]) <= 8192:
                digest = hashlib.sha256(supplied[0][7:]).hexdigest()
                for entry in entries:
                    if "token_sha256" in entry and hmac.compare_digest(entry["token_sha256"], digest):
                        identity = entry
                if identity is None and verifier is not None:
                    from .identity import IdentityError
                    try:
                        external = await asyncio.to_thread(verifier.subject, supplied[0][7:].decode("ascii"))
                        identity = next((entry for entry in entries if entry.get("oidc_subject") == external), None)
                    except (IdentityError, UnicodeDecodeError):
                        identity = None
            if identity is None:
                await JSONResponse({"detail": "valid bearer identity required"}, status_code=401,
                    headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
            if scope["path"] == "/session" and scope["method"] == "GET":
                await JSONResponse({"subject": identity["subject"], "role": identity["role"],
                    "authenticated": True}, headers={"Cache-Control": "no-store"})(scope, receive, send)
                return
            if identity["role"] == "reader" and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
                await JSONResponse({"detail": "researcher role required"}, status_code=403)(scope, receive, send)
                return
            def stream_authorized():
                # Called by the synchronous SSE generator in its worker thread.
                # A changed role closes the stream; reconnect uses the new role.
                try:
                    current, known, external_subjects = load_identity_records(identity_file)
                    if not known.issubset(children) or (external_subjects and verifier is None) or identity not in current:
                        return False
                    if "oidc_subject" in identity:
                        return verifier.subject(supplied[0][7:].decode("ascii")) == identity["oidc_subject"]
                    return True
                except (OSError, ValueError, TypeError):
                    return False

            scope["researchpilot.stream_authorized"] = stream_authorized
            await children[identity["subject"]](scope, receive, send)

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            for child in children.values():
                await stack.enter_async_context(child.router.lifespan_context(child))
            yield

    app = Starlette(routes=[Mount("/", app=Router())], lifespan=lifespan)
    origins = [s.strip() for s in os.environ.get("RESEARCHPILOT_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000").split(",") if s.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "Authorization"])
    return app
