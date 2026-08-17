async def test_liveness(client):
    resp = await client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_checks_the_database(client):
    """What the platform's health check gates on — a failure here means the
    database is unreachable, not merely that the process is up."""
    resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_security_headers_are_present(client):
    resp = await client.get("/api/v1/health/live")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    # HSTS is production-only: sending it over plain-HTTP local development
    # would pin the browser to https://localhost.
    assert "strict-transport-security" not in resp.headers


async def test_every_response_carries_a_request_id(client):
    """The id returned here is what appears in the logs and in error bodies —
    it is the only thing tying a user's report to a log line."""
    resp = await client.get("/api/v1/health/live")
    assert resp.headers.get("x-request-id")
