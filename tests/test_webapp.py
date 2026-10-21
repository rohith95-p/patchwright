"""Web API tests with FastAPI's TestClient. They start no runs and call no model."""
import pytest
from fastapi.testclient import TestClient

from webapp import app as webapp

client = TestClient(webapp.app)
REPO = "https://github.com/owner/repo.git"


def test_config_and_examples_endpoints():
    c = client.get("/api/config").json()
    assert set(c["models"]) == {"nano", "super", "ultra"} and "provider" in c
    ex = client.get("/api/examples").json()
    assert len(ex) == 20 and all({"id", "issue", "difficulty"} <= set(e) for e in ex)
    assert all("hidden" not in e and "reference" not in e for e in ex), "hidden tests must never leave the server"


def test_index_page_and_static_assets():
    assert client.get("/").status_code == 200
    for f in ("app.js", "styles.css"):
        assert client.get(f"/static/{f}").status_code == 200


@pytest.mark.parametrize("issue", ["i wanna know all the issues", "find all the bug and solve them", "fix everything please now", "too short"])
def test_vague_requests_are_rejected_before_any_tokens_are_spent(issue):
    before = len(webapp.RUNS)
    r = client.post("/api/runs", json={"repo_url": REPO, "issue": issue})
    assert r.status_code == 400
    assert len(webapp.RUNS) == before


@pytest.mark.parametrize("url", ["file:///etc/passwd", "C:\\secrets", "http://", "git@github.com:a/b.git", "https://x y/z"])
def test_non_https_or_malformed_repo_urls_are_rejected(url):
    r = client.post("/api/runs", json={"repo_url": url, "issue": "average([2, 4, 6]) returns 6 but should be 4 here"})
    assert r.status_code == 400


def test_bad_mode_and_unknown_example_and_unknown_run():
    assert client.post("/api/runs", json={"example_id": "avg-offbyone", "mode": "nope"}).status_code == 400
    assert client.post("/api/runs", json={"example_id": "does-not-exist"}).status_code == 404
    assert client.get("/api/runs/doesnotexist").status_code == 404


def test_demo_mode_blocks_custom_repos_and_benchmark_control(monkeypatch):
    monkeypatch.setattr(webapp, "DEMO_MODE", True)
    r = client.post("/api/runs", json={"repo_url": REPO, "issue": "average([2, 4, 6]) returns 6 but should be 4 here"})
    assert r.status_code == 403
    assert client.post("/api/bench/start", json={"config": "tiered"}).status_code == 403
    assert client.get("/api/config").json()["demo_mode"] is True


def test_busy_server_refuses_new_runs(monkeypatch):
    monkeypatch.setattr(webapp, "MAX_QUEUE", 0)
    r = client.post("/api/runs", json={"example_id": "avg-offbyone"})
    assert r.status_code == 429


def test_bench_endpoint_shape():
    b = client.get("/api/bench").json()
    assert {"results", "prices_are_estimates", "tasks"} <= set(b) and len(b["tasks"]) == 20
