"""Request-size and rate limits, and what they honestly protect against.

Both limits exist because the two ways to hurt this service are cheap for a
caller and expensive for the server: send a body large enough to matter, or
start more model calls than anyone would mean to. Each of these tests attempts
the thing and shows the attempt fail, which is the only kind of evidence a
control like this is worth having.

The limitations are tested too, and deliberately: the rate limiter's counters
live in this process's memory, so it is a brake rather than a wall. A test that
pretended otherwise would be worse than no test.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.api.limits import (
    MaxBodySizeMiddleware,
    RateLimit,
    client_key,
    reset_all_rate_limits,
)
from app.core.errors import register_exception_handlers


def _app(settings, *, limit: RateLimit | None = None, max_bytes: int | None = None) -> FastAPI:
    """A minimal app with only the limit under test wired in."""
    app = FastAPI()
    if max_bytes is not None:
        app.add_middleware(MaxBodySizeMiddleware, max_bytes=max_bytes)
    app.state.settings = settings
    register_exception_handlers(app)

    deps = [Depends(limit)] if limit is not None else []

    @app.post("/thing", dependencies=deps)
    def _thing() -> dict:
        return {"ok": True}

    return app


# --------------------------------------------------------------------------
# 1. Request size
# --------------------------------------------------------------------------


def test_a_body_larger_than_the_cap_is_refused_before_it_is_read(settings_factory) -> None:
    """413, not a 2 GB allocation. The point is the order of events."""
    client = TestClient(_app(settings_factory(), max_bytes=1024))

    response = client.post("/thing", content=b"x" * 4096)

    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert response.json()["details"]["max_bytes"] == 1024


def test_a_body_within_the_cap_passes(settings_factory) -> None:
    client = TestClient(_app(settings_factory(), max_bytes=1024))

    assert client.post("/thing", content=b"x" * 512).status_code == 200


def test_the_refusal_says_what_to_do_rather_than_only_that_it_failed(settings_factory) -> None:
    client = TestClient(_app(settings_factory(), max_bytes=1024))

    message = client.post("/thing", content=b"x" * 4096).json()["message"]

    assert "MB limit" in message
    assert "fewer CVs" in message


def test_a_get_is_never_refused_for_size(settings_factory) -> None:
    """The middleware must not touch reads; only bodies can be oversized."""
    app = _app(settings_factory(), max_bytes=1)

    @app.get("/read")
    def _read() -> dict:
        return {"ok": True}

    assert TestClient(app).get("/read").status_code == 200


def test_the_configured_cap_admits_a_full_batch_of_maximum_size_uploads(settings_factory) -> None:
    """A limit that refuses a legal request is a bug, not a control."""
    settings = settings_factory()
    largest_legal_batch = settings.max_files_per_batch * settings.max_upload_size_bytes

    assert settings.max_request_body_bytes >= largest_legal_batch


# --------------------------------------------------------------------------
# 2. Rate limiting
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_buckets():
    reset_all_rate_limits()
    yield
    reset_all_rate_limits()


def test_requests_beyond_the_limit_are_refused_with_a_retry_hint(settings_factory) -> None:
    limit = RateLimit("test", per_minute=3)
    client = TestClient(_app(settings_factory(), limit=limit))

    assert [client.post("/thing").status_code for _ in range(3)] == [200, 200, 200]

    refused = client.post("/thing")
    assert refused.status_code == 429
    assert refused.json()["code"] == "rate_limited"
    assert refused.json()["details"]["limit_per_minute"] == 3
    # A client that is told to back off needs to be told for how long.
    assert int(refused.headers["Retry-After"]) > 0


def test_the_limit_can_be_turned_off_entirely(settings_factory) -> None:
    """A single-user local run should not be rate limited at all."""
    limit = RateLimit("test", per_minute=1)
    client = TestClient(_app(settings_factory(rate_limit_enabled=False), limit=limit))

    assert [client.post("/thing").status_code for _ in range(5)] == [200] * 5


def test_a_bucket_with_no_limit_of_its_own_uses_the_configured_default(settings_factory) -> None:
    limit = RateLimit("test", per_minute=0)
    client = TestClient(_app(settings_factory(rate_limit_per_minute=2), limit=limit))

    assert [client.post("/thing").status_code for _ in range(3)] == [200, 200, 429]


def test_two_buckets_do_not_starve_each_other(settings_factory) -> None:
    """Uploading a lot must not stop a person extracting requirements."""
    cheap = RateLimit("cheap", per_minute=1)
    expensive = RateLimit("expensive", per_minute=1)
    app = _app(settings_factory(), limit=cheap)

    @app.post("/other", dependencies=[Depends(expensive)])
    def _other() -> dict:
        return {"ok": True}

    client = TestClient(app)
    assert client.post("/thing").status_code == 200
    assert client.post("/thing").status_code == 429
    # The other bucket is untouched by the first one's exhaustion.
    assert client.post("/other").status_code == 200


def test_separate_clients_have_separate_allowances(settings_factory) -> None:
    limit = RateLimit("test", per_minute=1)
    client = TestClient(_app(settings_factory(), limit=limit))

    assert client.post("/thing", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert client.post("/thing", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
    assert client.post("/thing", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200


def test_the_client_key_is_spoofable_and_that_is_recorded_here(settings_factory) -> None:
    """Not a bug report — a limitation, pinned so it cannot be forgotten.

    `X-Forwarded-For` is chosen by the caller. Anyone who wants a fresh
    allowance can have one by changing a header, which is exactly why this
    limiter is documented as a brake on accidental hammering rather than as a
    defence against a determined attacker.
    """
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1")],
        "client": ("127.0.0.1", 1234),
    }

    assert client_key(Request(scope)) == "203.0.113.9"


def test_without_a_forwarded_header_the_socket_peer_is_used() -> None:
    scope = {"type": "http", "headers": [], "client": ("198.51.100.7", 4321)}

    assert client_key(Request(scope)) == "198.51.100.7"


# --------------------------------------------------------------------------
# 3. The real endpoints, wired up
# --------------------------------------------------------------------------


@pytest.mark.requires_db
def test_the_model_backed_endpoints_are_actually_limited(api: TestClient, monkeypatch) -> None:
    """The limit is only worth anything if it is attached to the costly routes.

    Asserted through the API rather than by reading the decorator, so moving a
    route or forgetting the dependency on a new one is caught.
    """
    from app.api import limits

    monkeypatch.setattr(limits.model_calls, "per_minute", 2)

    job = api.post("/api/jobs", json={"title": "Rate limited"}).json()
    path = f"/api/jobs/{job['id']}/requirements/extract"

    codes = [api.post(path).status_code for _ in range(3)]

    # The first two fail for their own reasons (no description attached); what
    # matters is that the third never reaches the service at all.
    assert codes[-1] == 429
    assert 429 not in codes[:2]


@pytest.mark.requires_db
def test_reading_is_never_rate_limited(api: TestClient, monkeypatch) -> None:
    """Refreshing a page must not be able to lock someone out of their own data."""
    from app.api import limits

    monkeypatch.setattr(limits.model_calls, "per_minute", 1)
    monkeypatch.setattr(limits.uploads, "per_minute", 1)

    job = api.post("/api/jobs", json={"title": "Read a lot"}).json()

    codes = [api.get(f"/api/jobs/{job['id']}").status_code for _ in range(10)]

    assert codes == [200] * 10
