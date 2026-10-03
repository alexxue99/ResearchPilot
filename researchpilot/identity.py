"""Optional verification of issuer-signed JWT bearer access tokens."""
from __future__ import annotations

import os
from urllib.parse import urlsplit


class IdentityError(ValueError):
    pass


class OIDCVerifier:
    def __init__(self, issuer: str, audience: str, jwks_url: str, *, key_client=None):
        for value in (issuer, jwks_url):
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("OIDC issuer and JWKS URL must be explicit HTTPS URLs")
        if not audience:
            raise ValueError("OIDC audience must be configured")
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError("Install the 'identity' extra to verify OIDC tokens") from exc
        self.jwt = jwt
        self.issuer, self.audience = issuer, audience
        self.keys = key_client or jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300, timeout=10)

    @classmethod
    def from_env(cls):
        values = [os.environ.get(name, "") for name in (
            "RESEARCHPILOT_OIDC_ISSUER", "RESEARCHPILOT_OIDC_AUDIENCE", "RESEARCHPILOT_OIDC_JWKS_URL")]
        if not all(values):
            raise ValueError("OIDC requires issuer, audience, and JWKS URL")
        return cls(*values)

    def subject(self, token: str) -> str:
        try:
            header = self.jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not 1 <= len(header["kid"]) <= 256:
                raise IdentityError("invalid bearer identity")
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = self.jwt.decode(token, key, algorithms=["RS256"], issuer=self.issuer,
                audience=self.audience, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            subject = claims["sub"]
            if not isinstance(subject, str) or not subject or len(subject) > 512:
                raise IdentityError("invalid bearer identity")
            return subject
        except (self.jwt.PyJWTError, ValueError, TypeError, OSError) as exc:
            raise IdentityError("invalid bearer identity") from exc
