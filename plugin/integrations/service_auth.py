"""Fail-closed KitsuSync v1 service-bearer verification.

The verifier accepts only the contract's RS256 JWT profile. It never follows
token-provided key URLs; the JWKS location is derived from the configured,
canonical HTTPS issuer.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass
from functools import wraps
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


JWKS_PATH = "/.well-known/vfx-files-jwks.json"
MAX_JWKS_BYTES = 64 * 1024
MAX_JWKS_KEYS = 10
MAX_TOKEN_LIFETIME = 300
CLOCK_SKEW = 30
ALLOWED_SCOPES = frozenset({
    "vfx.connections.read",
    "vfx.connections.authorize",
    "vfx.connections.revoke",
    "vfx.roots.read",
    "vfx.roots.verify",
    "vfx.templates.read",
    "vfx.config.read",
    "vfx.config.write",
    "vfx.activation.write",
    "vfx.sync.preview",
    "vfx.sync.apply",
    "vfx.sync.read",
    "vfx.sync.retry",
    "vfx.sync.reconcile",
    "vfx.sync.snapshot.write",
})
PLUGIN_AUDIENCE_PATH = "/api/plugins/vfx_files/integrations/v1"


class ServiceAuthenticationError(Exception):
    """Sanitized service-auth rejection with an HTTP status classification."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class ServiceIdentity:
    production_id: UUID
    actor_id: UUID
    actor_role: str
    scopes: frozenset[str]
    subject: str
    client_id: str


def _decode_segment(segment: str) -> bytes:
    if not segment or len(segment) > 90_000 or "=" in segment:
        raise ValueError("invalid segment")
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
        raise ValueError("invalid segment")
    padded = segment + "=" * (-len(segment) % 4)
    decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != segment:
        raise ValueError("non-canonical segment")
    return decoded


def _decode_json(segment: str) -> dict:
    def reject_duplicate_members(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    value = json.loads(_decode_segment(segment), object_pairs_hook=reject_duplicate_members)
    if not isinstance(value, dict):
        raise ValueError("invalid JSON object")
    return value


def _canonical_issuer(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("issuer must be a canonical HTTPS origin")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("issuer must be a canonical HTTPS origin")
    if parsed.port is not None:
        raise ValueError("issuer must not contain an explicit port")
    host = parsed.hostname
    if not host or host != host.lower():
        raise ValueError("issuer host is not canonical")
    return f"https://{parsed.netloc.lower()}"


def _canonical_audience(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname != parsed.hostname.lower()
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != PLUGIN_AUDIENCE_PATH
        or parsed.query
        or parsed.fragment
        or parsed.netloc != parsed.hostname
    ):
        raise ValueError("audience is not canonical")
    return f"https://{parsed.hostname}{PLUGIN_AUDIENCE_PATH}"


def _jwk_key(value: dict):
    if (
        value.get("kty") != "RSA"
        or value.get("use", "sig") != "sig"
        or value.get("alg", "RS256") != "RS256"
        or not isinstance(value.get("kid"), str)
        or not value["kid"]
        or len(value["kid"]) > 128
    ):
        return None
    modulus = int.from_bytes(_decode_segment(value["n"]), "big")
    exponent = int.from_bytes(_decode_segment(value["e"]), "big")
    if modulus.bit_length() < 2048 or exponent < 3 or exponent % 2 == 0:
        return None
    return rsa.RSAPublicNumbers(exponent, modulus).public_key()


class JWKSCache:
    """Small bounded JWKS cache; unknown kids trigger exactly one refresh."""

    def __init__(self, issuer: str, *, fetcher=None, cache_seconds: int = 300, clock=time.time):
        self.issuer = _canonical_issuer(issuer)
        self.url = self.issuer + JWKS_PATH
        self.fetcher = fetcher or self._fetch
        self.cache_seconds = min(max(int(cache_seconds), 1), 300)
        self.clock = clock
        self._keys: dict[str, object] = {}
        self._expires_at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _fetch(url: str) -> bytes:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, request, response, code, message, headers, new_url):
                return None

        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        opener = build_opener(NoRedirect())
        with opener.open(request, timeout=3) as response:
            if response.status != 200:
                raise ValueError("JWKS unavailable")
            body = response.read(MAX_JWKS_BYTES + 1)
            if len(body) > MAX_JWKS_BYTES:
                raise ValueError("JWKS exceeds size limit")
            return body

    def _refresh(self):
        body = self.fetcher(self.url)
        if isinstance(body, dict):
            document = body
        else:
            if not isinstance(body, (bytes, bytearray)) or len(body) > MAX_JWKS_BYTES:
                raise ValueError("JWKS exceeds size limit")
            document = json.loads(body)
        keys = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_JWKS_KEYS:
            raise ValueError("JWKS key set is invalid")
        verified = {}
        for item in keys:
            if not isinstance(item, dict):
                raise ValueError("JWKS key is invalid")
            key = _jwk_key(item)
            if key is not None:
                kid = item["kid"]
                if kid in verified:
                    raise ValueError("JWKS kid is duplicated")
                verified[kid] = key
        if not verified:
            raise ValueError("JWKS has no acceptable signing key")
        self._keys = verified
        self._expires_at = self.clock() + self.cache_seconds

    def key_for(self, kid: str):
        with self._lock:
            if self.clock() >= self._expires_at:
                self._refresh()
            key = self._keys.get(kid)
            if key is None:
                # One bounded refresh permits normal key rotation while making
                # an arbitrary kid unable to trigger repeated network access.
                self._refresh()
                key = self._keys.get(kid)
            if key is None:
                raise ValueError("unknown signing key")
            return key


def verify_service_bearer(
    authorization: str | None,
    actor_header: str | None,
    *,
    issuer: str,
    audience: str,
    subject: str,
    client_id: str,
    required_scope: str,
    requested_production_id: str,
    jwks: JWKSCache,
    now=None,
) -> ServiceIdentity:
    """Verify token and v1 request binding; errors contain no token material."""
    try:
        if _canonical_issuer(issuer) != issuer:
            raise ValueError("issuer is not canonical")
        if _canonical_audience(audience) != audience:
            raise ValueError("audience is not the canonical Plugin API URL")
        if not authorization or not authorization.startswith("Bearer "):
            raise ValueError("missing bearer")
        token = authorization[7:]
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("malformed token")
        header = _decode_json(parts[0])
        claims = _decode_json(parts[1])
        if header.get("typ") != "at+JWT" or header.get("alg") != "RS256":
            raise ValueError("unsupported token header")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ValueError("missing kid")
        if set(header) - {"typ", "alg", "kid"}:
            raise ValueError("unsupported token header field")
        key = jwks.key_for(kid)
        key.verify(
            _decode_segment(parts[2]),
            (parts[0] + "." + parts[1]).encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        current = time.time() if now is None else now
        required = {"iss", "aud", "sub", "client_id", "iat", "nbf", "exp", "jti", "scope", "production_id", "actor_id", "actor_role"}
        if not required <= set(claims):
            raise ValueError("required claim missing")
        times = (claims["iat"], claims["nbf"], claims["exp"])
        if any(not isinstance(v, int) or isinstance(v, bool) for v in times):
            raise ValueError("invalid time claim")
        iat, nbf, exp = times
        if iat > current + CLOCK_SKEW or nbf > current + CLOCK_SKEW or exp < current - CLOCK_SKEW or exp <= iat or exp - iat > MAX_TOKEN_LIFETIME:
            raise ValueError("invalid token lifetime")
        if claims["iss"] != issuer or claims["aud"] != audience or claims["sub"] != subject or claims["client_id"] != client_id:
            raise ValueError("token identity mismatch")
        if not isinstance(claims["jti"], str) or not claims["jti"] or len(claims["jti"]) > 200:
            raise ValueError("invalid token identity")
        production_id = UUID(claims["production_id"])
        actor_id = UUID(claims["actor_id"])
        if str(production_id) != claims["production_id"] or str(actor_id) != claims["actor_id"]:
            raise ValueError("noncanonical UUID")
    except ServiceAuthenticationError:
        raise
    except Exception:
        raise ServiceAuthenticationError(401, "invalid_service_token") from None

    if claims["actor_role"] != "production_admin":
        raise ServiceAuthenticationError(403, "forbidden")
    scopes = claims["scope"].split(" ") if isinstance(claims["scope"], str) else []
    if not isinstance(claims["scope"], str) or not scopes or any(not scope for scope in scopes) or len(set(scopes)) != len(scopes) or not set(scopes) <= ALLOWED_SCOPES:
        raise ServiceAuthenticationError(401, "invalid_service_token")
    if required_scope not in scopes or str(production_id) != str(requested_production_id):
        raise ServiceAuthenticationError(403, "forbidden")
    if actor_header != claims.get("actor_id"):
        raise ServiceAuthenticationError(401, "invalid_service_token")
    try:
        header_actor = UUID(actor_header or "")
    except (ValueError, TypeError, AttributeError):
        raise ServiceAuthenticationError(401, "invalid_service_token") from None
    if header_actor != actor_id:
        raise ServiceAuthenticationError(401, "invalid_service_token")
    return ServiceIdentity(production_id, actor_id, claims["actor_role"], frozenset(scopes), subject, client_id)


def authenticate_management_request(required_scope: str, requested_production_id: str) -> ServiceIdentity:
    """Authenticate the current Flask request using deployment-owned settings."""
    from flask import current_app, request

    settings = current_app.config
    issuer = settings.get("VFX_FILES_KITSUSYNC_ISSUER")
    audience = settings.get("VFX_FILES_KITSUSYNC_AUDIENCE")
    subject = settings.get("VFX_FILES_KITSUSYNC_SUBJECT")
    client_id = settings.get("VFX_FILES_KITSUSYNC_CLIENT_ID")
    if not all(isinstance(value, str) and value for value in (issuer, audience, subject, client_id)):
        raise ServiceAuthenticationError(401, "service_auth_not_configured")
    cache_key = "vfx_files_kitsusync_jwks_cache"
    cache = current_app.extensions.get(cache_key)
    if cache is None:
        try:
            cache = JWKSCache(issuer)
        except ValueError:
            raise ServiceAuthenticationError(401, "service_auth_not_configured") from None
        current_app.extensions[cache_key] = cache
    return verify_service_bearer(
        request.headers.get("Authorization"),
        request.headers.get("X-KitsuSync-Actor-ID"),
        issuer=issuer,
        audience=audience,
        subject=subject,
        client_id=client_id,
        required_scope=required_scope,
        requested_production_id=requested_production_id,
        jwks=cache,
    )


def service_scope(required_scope: str):
    """Flask view decorator for management operations with a Production path."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            from flask import jsonify

            production_id = kwargs.get("production_id")
            try:
                identity = authenticate_management_request(required_scope, production_id)
            except ServiceAuthenticationError as exc:
                response = jsonify({"contract_version": "1.0", "error": {"code": exc.code}})
                return response, exc.status
            kwargs["service_identity"] = identity
            return view(*args, **kwargs)
        return wrapped
    return decorate
