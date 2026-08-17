async def test_register_login_me(client, auth_tokens):
    resp = await client.get("/api/v1/users/me",
                            headers={"Authorization": f"Bearer {auth_tokens['access_token']}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert "password" not in resp.text


async def test_duplicate_registration_conflicts(client, registered_user):
    resp = await client.post("/api/v1/auth/register", json=registered_user)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_wrong_password_uniform_error(client, registered_user):
    resp = await client.post("/api/v1/auth/login", json={
        "email": registered_user["email"], "password": "nope-nope-nope"})
    assert resp.status_code == 401
    unknown = await client.post("/api/v1/auth/login", json={
        "email": "ghost@example.com", "password": "nope-nope-nope"})
    assert unknown.status_code == 401
    # identical envelope: no account enumeration
    assert resp.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_refresh_rotates_and_detects_reuse(client, auth_tokens):
    first = auth_tokens["refresh_token"]
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert r1.status_code == 200
    second = r1.json()["refresh_token"]
    assert second != first

    # Replaying the rotated token must fail AND kill the family.
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401
    dead = await client.post("/api/v1/auth/refresh", json={"refresh_token": second})
    assert dead.status_code == 401


async def test_logout_revokes(client, auth_tokens):
    rt = auth_tokens["refresh_token"]
    assert (await client.post("/api/v1/auth/logout", json={"refresh_token": rt})).status_code == 204
    replayed = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
    assert replayed.status_code == 401


async def test_account_deletion_kills_sessions(client, auth_tokens):
    headers = {"Authorization": f"Bearer {auth_tokens['access_token']}"}
    assert (await client.delete("/api/v1/users/me", headers=headers)).status_code == 204
    # user.deleted event → auth subscriber revoked all refresh tokens
    resp = await client.post("/api/v1/auth/refresh",
                             json={"refresh_token": auth_tokens["refresh_token"]})
    assert resp.status_code == 401
    # access token now resolves to a soft-deleted user → 404 from repository
    assert (await client.get("/api/v1/users/me", headers=headers)).status_code in (401, 404)
