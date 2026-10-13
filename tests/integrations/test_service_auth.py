import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from plugin.integrations.service_auth import JWKSCache, ServiceAuthenticationError, verify_service_bearer


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@pytest.fixture
def service_token_factory():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_numbers()
    kid = "test-key-1"
    jwk = {
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
        "n": _b64(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
        "e": _b64(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
    }

    def issue(**overrides):
        claims = {
            "iss": "https://sync.example.test",
            "aud": "https://files.example.test/api/plugins/vfx_files/integrations/v1",
            "sub": "kitsusync-service",
            "client_id": "installation-1",
            "iat": 1_800_000_000,
            "nbf": 1_800_000_000,
            "exp": 1_800_000_120,
            "jti": "request-unique-1",
            "scope": "vfx.config.read vfx.config.write",
            "production_id": "10000000-0000-4000-8000-000000000001",
            "actor_id": "20000000-0000-4000-8000-000000000002",
            "actor_role": "production_admin",
        }
        claims.update(overrides)
        header = {"typ": "at+JWT", "alg": "RS256", "kid": kid}
        encoded = _b64(json.dumps(header, separators=(",", ":")).encode()) + "." + _b64(json.dumps(claims, separators=(",", ":")).encode())
        signature = private.sign(encoded.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return encoded + "." + _b64(signature)

    return issue, jwk


def _verify(token, jwk, **kwargs):
    fetches = []

    def fetch(url):
        fetches.append(url)
        return {"keys": [jwk]}

    cache = JWKSCache("https://sync.example.test", fetcher=fetch)
    identity = verify_service_bearer(
        "Bearer " + token,
        "20000000-0000-4000-8000-000000000002",
        issuer="https://sync.example.test",
        audience="https://files.example.test/api/plugins/vfx_files/integrations/v1",
        subject="kitsusync-service",
        client_id="installation-1",
        required_scope="vfx.config.read",
        requested_production_id="10000000-0000-4000-8000-000000000001",
        jwks=cache,
        now=1_800_000_010,
        **kwargs,
    )
    return identity, fetches


def test_service_bearer_verifies_published_rs256_profile(service_token_factory):
    issue, jwk = service_token_factory
    identity, fetches = _verify(issue(), jwk)
    assert str(identity.production_id) == "10000000-0000-4000-8000-000000000001"
    assert "vfx.config.read" in identity.scopes
    assert fetches == ["https://sync.example.test/.well-known/vfx-files-jwks.json"]


@pytest.mark.parametrize("header", [
    {"typ": "JWT", "alg": "RS256", "kid": "test-key-1"},
    {"typ": "at+JWT", "alg": "HS256", "kid": "test-key-1"},
    {"typ": "at+JWT", "alg": "RS256", "kid": "test-key-1", "jku": "https://attacker.test/key"},
])
def test_service_bearer_rejects_noncontract_headers(service_token_factory, header):
    issue, jwk = service_token_factory
    token = issue()
    pieces = token.split(".")
    pieces[0] = _b64(json.dumps(header, separators=(",", ":")).encode())
    # The tampered signature must be rejected even when a header is malformed.
    with pytest.raises(ServiceAuthenticationError) as error:
        _verify(".".join(pieces), jwk)
    assert error.value.status == 401


@pytest.mark.parametrize(("claims", "actor", "scope", "production", "status"), [
    ({"exp": 1_800_000_400}, "20000000-0000-4000-8000-000000000002", "vfx.config.read", "10000000-0000-4000-8000-000000000001", 401),
    ({"actor_role": "artist"}, "20000000-0000-4000-8000-000000000002", "vfx.config.read", "10000000-0000-4000-8000-000000000001", 403),
    ({}, "20000000-0000-4000-8000-000000000002", "vfx.sync.apply", "10000000-0000-4000-8000-000000000001", 403),
    ({}, "20000000-0000-4000-8000-000000000002", "vfx.config.read", "10000000-0000-4000-8000-000000000003", 403),
    ({}, "30000000-0000-4000-8000-000000000003", "vfx.config.read", "10000000-0000-4000-8000-000000000001", 401),
])
def test_service_bearer_fails_closed_on_lifetime_scope_production_and_actor(service_token_factory, claims, actor, scope, production, status):
    issue, jwk = service_token_factory
    token = issue(**claims)
    cache = JWKSCache("https://sync.example.test", fetcher=lambda _url: {"keys": [jwk]})
    with pytest.raises(ServiceAuthenticationError) as error:
        verify_service_bearer(
            "Bearer " + token, actor,
            issuer="https://sync.example.test",
            audience="https://files.example.test/api/plugins/vfx_files/integrations/v1",
            subject="kitsusync-service", client_id="installation-1",
            required_scope=scope, requested_production_id=production,
            jwks=cache, now=1_800_000_010,
        )
    assert error.value.status == status
    assert str(error.value) in {"unauthorized_service", "production_access_denied", "invalid_scope"}


@pytest.mark.parametrize("scope", ["vfx.config.read vfx.unknown", "vfx.config.read vfx.config.read", "vfx.config.read  vfx.config.write"])
def test_service_bearer_rejects_unknown_duplicate_and_empty_scopes(service_token_factory, scope):
    issue, jwk = service_token_factory
    with pytest.raises(ServiceAuthenticationError) as error:
        _verify(issue(scope=scope), jwk)
    assert error.value.status == 401


@pytest.mark.parametrize("audience", [
    "http://files.example.test/api/plugins/vfx_files/integrations/v1",
    "https://FILES.example.test/api/plugins/vfx_files/integrations/v1",
    "https://files.example.test:443/api/plugins/vfx_files/integrations/v1",
    "https://files.example.test/api/plugins/vfx_files/integrations/v1/",
    "https://user@files.example.test/api/plugins/vfx_files/integrations/v1",
    "https://files.example.test/api/plugins/vfx_files/integrations/v1?x=1",
])
def test_service_bearer_rejects_noncanonical_audience(service_token_factory, audience):
    issue, jwk = service_token_factory
    token = issue(aud=audience)
    with pytest.raises(ServiceAuthenticationError) as error:
        verify_service_bearer(
            "Bearer " + token,
            "20000000-0000-4000-8000-000000000002",
            issuer="https://sync.example.test",
            audience=audience,
            subject="kitsusync-service",
            client_id="installation-1",
            required_scope="vfx.config.read",
            requested_production_id="10000000-0000-4000-8000-000000000001",
            jwks=JWKSCache("https://sync.example.test", fetcher=lambda _url: {"keys": [jwk]}),
            now=1_800_000_010,
        )
    assert error.value.status == 401


def test_jwks_unknown_kid_refreshes_once_then_fails(service_token_factory):
    issue, jwk = service_token_factory
    token = issue()
    header, payload, signature = token.split(".")
    header = _b64(json.dumps({"typ": "at+JWT", "alg": "RS256", "kid": "unknown"}).encode())
    fetches = []
    cache = JWKSCache("https://sync.example.test", fetcher=lambda url: (fetches.append(url) or {"keys": [jwk]}))
    with pytest.raises(ServiceAuthenticationError) as error:
        verify_service_bearer(
            "Bearer " + ".".join((header, payload, signature)),
            "20000000-0000-4000-8000-000000000002",
            issuer="https://sync.example.test",
            audience="https://files.example.test/api/plugins/vfx_files/integrations/v1",
            subject="kitsusync-service", client_id="installation-1",
            required_scope="vfx.config.read",
            requested_production_id="10000000-0000-4000-8000-000000000001",
            jwks=cache, now=1_800_000_010,
        )
    assert error.value.status == 401
    assert len(fetches) == 2


@pytest.mark.parametrize("issuer", ["http://sync.example.test", "https://sync.example.test/path", "https://user@sync.example.test", "https://sync.example.test:444", "https://sync.example.test:443"])
def test_jwks_issuer_must_be_canonical_https_origin(issuer):
    with pytest.raises(ValueError):
        JWKSCache(issuer, fetcher=lambda _url: {"keys": []})
