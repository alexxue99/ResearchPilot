import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from researchpilot.api import create_app


class WorkspaceAccessTests(unittest.TestCase):
    def configure(self, root):
        path = Path(root) / "identities.json"
        identities = [{"subject": subject, "role": role, "token_sha256": hashlib.sha256(token.encode()).hexdigest()}
                      for subject, role, token in [("alice", "researcher", "alice-write"),
                          ("alice", "reader", "alice-read"), ("bob", "researcher", "bob-write")]]
        path.write_text(json.dumps(identities), encoding="utf-8")
        return {"RESEARCHPILOT_IDENTITIES_FILE": str(path), "RESEARCHPILOT_EXECUTOR": "docker"}

    def test_cross_user_research_papers_and_jobs_are_inaccessible(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, self.configure(tmp)), TestClient(create_app(tmp)) as client:
            alice = {"Authorization": "Bearer alice-write"}; bob = {"Authorization": "Bearer bob-write"}
            state = client.post("/research", headers=alice, json={"question": "Plan a scoped research investigation."}).json()
            rid = state["id"]
            self.assertEqual(client.get("/research", headers=bob).json(), [])
            for suffix in ("", "/sources", "/retrieval?q=variance", "/job", "/trace", "/artifact/0"):
                self.assertEqual(client.get(f"/research/{rid}{suffix}", headers=bob).status_code, 404)
            paper = {"filename": "paper.md", "title": "Private", "content_base64": base64.b64encode(b"# Results\nSecret variance measurements.").decode()}
            self.assertEqual(client.post(f"/research/{rid}/papers", headers=alice, json=paper).status_code, 200)
            self.assertEqual(client.post(f"/research/{rid}/papers", headers=bob, json=paper).status_code, 404)
            self.assertEqual(len(list((Path(tmp) / "users").glob("*/researchpilot.db"))), 2)

    def test_reader_can_view_but_cannot_mutate(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, self.configure(tmp)), TestClient(create_app(tmp)) as client:
            writer = {"Authorization": "Bearer alice-write"}; reader = {"Authorization": "Bearer alice-read"}
            rid = client.post("/research", headers=writer, json={"question": "Plan a scoped investigation."}).json()["id"]
            self.assertEqual(client.get(f"/research/{rid}", headers=reader).status_code, 200)
            for suffix in ("continue", "cancel", "feedback", "papers", "plan"):
                self.assertEqual(client.post(f"/research/{rid}/{suffix}", headers=reader, json={}).status_code, 403)
            self.assertEqual(client.get("/research").status_code, 401)
            self.assertEqual(client.get("/health").status_code, 200)

    def test_invalid_config_and_local_executor_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.configure(tmp)
            with patch.dict(os.environ, {**config, "RESEARCHPILOT_EXECUTOR": "local"}):
                with self.assertRaises(ValueError): create_app(tmp)

    def test_rotation_role_changes_and_invalid_reload_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.configure(tmp)
            path = Path(config["RESEARCHPILOT_IDENTITIES_FILE"])
            with patch.dict(os.environ, config), TestClient(create_app(tmp)) as client:
                old = {"Authorization": "Bearer alice-write"}
                new = {"Authorization": "Bearer rotated-token"}
                self.assertEqual(client.get("/session", headers=old).json()["role"], "researcher")
                entries = json.loads(path.read_text())
                entries[0].update(role="reader", token_sha256=hashlib.sha256(b"rotated-token").hexdigest())
                path.write_text(json.dumps(entries))
                self.assertEqual(client.get("/session", headers=old).status_code, 401)
                self.assertEqual(client.get("/session", headers=new).json()["role"], "reader")
                self.assertEqual(client.post("/research", headers=new, json={}).status_code, 403)
                path.write_text("{")
                self.assertEqual(client.get("/session", headers=new).status_code, 503)
                path.write_text(json.dumps(entries))
                self.assertEqual(client.get("/session", headers=new).status_code, 200)
                entries[0]["subject"] = "new-workspace"
                path.write_text(json.dumps(entries))
                self.assertEqual(client.get("/session", headers=new).status_code, 503)
            Path(config["RESEARCHPILOT_IDENTITIES_FILE"]).write_text('[{"subject":"x","role":"admin","token_sha256":"invalid"}]')
            with patch.dict(os.environ, config):
                with self.assertRaises(ValueError): create_app(tmp)
