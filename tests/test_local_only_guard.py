"""HUB-01: the server the hub starts (DEMO=0) must refuse cross-site and DNS-rebound requests.

In the local posture current_user() mints the implicit "local" session for any caller, so the
SameSite=lax cookie protects nothing: a page on any site could send a "simple" cross-site POST
(text/plain body, no CORS preflight) to 127.0.0.1:<port>/api/creds -- `await request.json()` ignores
the Content-Type -- and re-point the LLM endpoint at its own server; /api/run would then execute that
"model's" run_python on the user's PC. DEMO=1 (public Cloud Run) keeps its own door and is untouched.
"""
import json, os, sys, tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="steltic-cfs-guard-test-"))
os.environ.setdefault("EXECUTOR", "subprocess")
os.environ.setdefault("DEMO", "0")

from fastapi.testclient import TestClient   # noqa: E402
from steltic import main, auth, config      # noqa: E402

EVIL_CREDS = json.dumps({"base_url": "https://attacker.example/v1", "api_key": "k", "model": "m"})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("STELTIC_HUB_TOKEN", raising=False)
    monkeypatch.delenv("STELTIC_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(config, "DEMO_MODE", False)
    return TestClient(main.app)


def _base_url():
    return (auth.get_creds("local") or {}).get("base_url")


def test_cross_site_post_cannot_repoint_the_llm_endpoint(client):
    before = _base_url()
    for h in ({"origin": "https://evil.example"}, {"origin": "null"}, {"sec-fetch-site": "cross-site"}):
        r = client.post("/api/creds", content=EVIL_CREDS, headers={"content-type": "text/plain", **h})
        assert r.status_code == 403, h
        assert _base_url() == before
    r = client.post("/api/run", content="{}", headers={"content-type": "text/plain", "origin": "https://evil.example"})
    assert r.status_code == 403 and "cross-site" in r.json()["detail"]
    assert client.post("/api/stop", content="{}", headers={"origin": "https://evil.example"}).status_code == 403


def test_dns_rebinding_host_is_refused_even_for_reads(client):
    assert client.get("/api/me", headers={"host": "attacker.example"}).status_code == 403
    assert client.get("/api/me", headers={"host": "attacker.example:8411"}).status_code == 403
    for h in ("127.0.0.1:8411", "localhost:8411", "[::1]:8411"):
        assert client.get("/healthz", headers={"host": h}).status_code == 200, h


def test_own_page_hub_proxy_and_local_clients_still_work(client):
    good = json.dumps({"base_url": "http://127.0.0.1:9/v1", "api_key": "k", "model": "MOCK"})
    for h in ({"origin": "http://127.0.0.1:8411", "sec-fetch-site": "same-origin"},
              {"origin": "http://localhost:8300", "sec-fetch-site": "same-origin"},
              {}):
        r = client.post("/api/creds", content=good, headers={"content-type": "application/json", **h})
        assert r.status_code < 400, (h, r.text)
    assert client.get("/api/me", headers={"origin": "https://evil.example"}).status_code == 200


def test_hub_token_is_checked_when_sent(client, monkeypatch):
    monkeypatch.setenv("STELTIC_HUB_TOKEN", "s3cret")
    good = json.dumps({"base_url": "http://127.0.0.1:9/v1", "api_key": "k", "model": "MOCK"})
    assert client.post("/api/creds", content=good, headers={"x-steltic-hub-token": "s3cret"}).status_code < 400
    r = client.post("/api/creds", content=good, headers={"x-steltic-hub-token": "guess"})
    assert r.status_code == 403 and "token" in r.json()["detail"]
    assert client.get("/api/me", headers={"x-steltic-hub-token": "s3cret", "host": "attacker.example"}).status_code == 403


def test_demo_posture_is_not_loopback_and_keeps_its_own_door(client, monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    assert client.get("/healthz", headers={"host": "steltic-cfs.example.run.app"}).status_code == 200
