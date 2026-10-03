import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from researchpilot.api import create_app
from researchpilot.identity import IdentityError, OIDCVerifier


HAS_CRYPTO = importlib.util.find_spec("jwt") is not None and importlib.util.find_spec("cryptography") is not None


@unittest.skipUnless(HAS_CRYPTO, "optional identity dependencies")
class IdentityVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public = cls.private.public_key()

    def verifier(self):
        keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=self.public))
        return OIDCVerifier("https://issuer.example", "researchpilot-api", "https://issuer.example/jwks", key_client=keys)

    def token(self, changes=None, algorithm="RS256"):
        import jwt
        now = int(time.time())
        claims = {"iss": "https://issuer.example", "aud": "researchpilot-api", "sub": "external-alice",
                  "iat": now - 1, "exp": now + 60}
        claims.update(changes or {})
        return jwt.encode(claims, self.private if algorithm == "RS256" else "a" * 32,
                          algorithm=algorithm, headers={"kid": "test-key"})

    def test_real_signature_and_required_claims(self):
        verifier = self.verifier()
        self.assertEqual(verifier.subject(self.token()), "external-alice")
        for changes in ({"exp": 0}, {"aud": "wrong"}, {"iss": "https://other.example"},
                        {"iat": int(time.time()) + 1000}, {"sub": ""}):
            with self.subTest(changes=changes), self.assertRaises(IdentityError):
                verifier.subject(self.token(changes))
        with self.assertRaises(IdentityError): verifier.subject(self.token(algorithm="HS256"))
        with self.assertRaises(IdentityError): verifier.subject("not.a.valid-token")

    def test_bad_signature_and_missing_expiry_are_rejected(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = int(time.time())
        claims = {"iss": "https://issuer.example", "aud": "researchpilot-api", "sub": "x", "iat": now, "exp": now + 60}
        invalid = jwt.encode(claims, other_key, algorithm="RS256", headers={"kid": "test-key"})
        with self.assertRaises(IdentityError): self.verifier().subject(invalid)
        del claims["exp"]
        invalid = jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": "test-key"})
        with self.assertRaises(IdentityError): self.verifier().subject(invalid)

    def test_gateway_uses_local_roles_and_rejects_unmapped_subjects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identities.json"
            path.write_text(json.dumps([{"subject": "alice", "role": "reader", "oidc_subject": "external-alice"}]))
            config = {"RESEARCHPILOT_IDENTITIES_FILE": str(path), "RESEARCHPILOT_EXECUTOR": "docker"}
            with patch.dict(os.environ, config), patch.object(OIDCVerifier, "from_env", return_value=self.verifier()), TestClient(create_app(tmp)) as client:
                headers = {"Authorization": "Bearer " + self.token({"role": "admin"})}
                self.assertEqual(client.get("/research", headers=headers).status_code, 200)
                self.assertEqual(client.post("/research", headers=headers, json={"question": "plan something"}).status_code, 403)
                headers = {"Authorization": "Bearer " + self.token({"sub": "unmapped"})}
                self.assertEqual(client.get("/research", headers=headers).status_code, 401)

    def test_configuration_requires_https(self):
        with self.assertRaises(ValueError): OIDCVerifier("http://issuer.example", "api", "https://issuer.example/jwks")

    def test_stream_rechecks_jwt_validity_after_initial_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'identities.json'
            path.write_text(json.dumps([{'subject': 'alice', 'role': 'researcher', 'oidc_subject': 'external-alice'}]))
            verifier = self.verifier()
            config = {'RESEARCHPILOT_IDENTITIES_FILE': str(path), 'RESEARCHPILOT_EXECUTOR': 'docker'}
            with patch.dict(os.environ, config), patch.object(OIDCVerifier, 'from_env', return_value=verifier), TestClient(create_app(tmp)) as client:
                headers = {'Authorization': 'Bearer ' + self.token()}
                rid = client.post('/research', headers=headers, json={'question': 'Plan a scoped investigation.'}).json()['id']
                with patch.object(verifier, 'subject', side_effect=['external-alice', IdentityError('expired')]):
                    response = client.get(f'/research/{rid}/events', headers=headers)
                self.assertIn('stream authorization ended', response.text)
                self.assertNotIn('event: trace', response.text)
                self.assertNotIn('event: state', response.text)
