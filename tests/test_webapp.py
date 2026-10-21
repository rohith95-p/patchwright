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


def test_demo_mode_caps_runs_per_visitor_and_per_day_to_protect_credits(monkeypatch):
    monkeypatch.setattr(webapp, "DEMO_MODE", True)
    monkeypatch.setattr(webapp, "_worker", lambda *a, **k: None)          # never call a model in this test
    monkeypatch.setattr(webapp, "RATE", {})
    monkeypatch.setattr(webapp, "DEMO_MAX_PER_IP_HOUR", 2)
    monkeypatch.setattr(webapp, "RUNS", {})
    ok = [client.post("/api/runs", json={"example_id": "avg-offbyone"}).status_code for _ in range(3)]
    assert ok == [200, 200, 429], "the third run from the same visitor within the hour must be refused"
    monkeypatch.setattr(webapp, "RATE", {})
    monkeypatch.setattr(webapp, "DEMO_MAX_PER_DAY", 2)                     # two runs already exist today
    r = client.post("/api/runs", json={"example_id": "avg-offbyone"})
    assert r.status_code == 429 and "daily limit" in r.json()["detail"]


def test_limits_do_not_apply_outside_demo_mode(monkeypatch):
    monkeypatch.setattr(webapp, "DEMO_MODE", False)
    monkeypatch.setattr(webapp, "_worker", lambda *a, **k: None)
    monkeypatch.setattr(webapp, "RATE", {})
    monkeypatch.setattr(webapp, "RUNS", {})
    monkeypatch.setattr(webapp, "DEMO_MAX_PER_IP_HOUR", 1)
    assert [client.post("/api/runs", json={"example_id": "avg-offbyone"}).status_code for _ in range(3)] == [200, 200, 200]


def test_showcase_is_computed_from_the_committed_results():
    d = client.get("/api/showcase").json()
    assert set(d["bench"]) == {"tiered", "routed", "super", "ultra"} and all(v["runs"] == 40 for v in d["bench"].values())
    assert d["bench"]["super"]["rate"] < d["bench"]["tiered"]["rate"], "Super alone must be weaker than tiered in the committed results"
    for c in ("tiered", "ultra"):
        assert d["efficiency"][c]["after"]["cost_fix"] < d["efficiency"][c]["before"]["cost_fix"]
    rw = d["realworld"]
    assert len(rw["attempts"]) == 7 and rw["solved_runs"] == 2, "the failures must stay visible"
    assert [a["run"] for a in rw["attempts"]] == [1, 2, 3, 4, 5, 6, 7] and all(a["lesson"] for a in rw["attempts"])
    assert rw["pr_url"].endswith("/pull/1") and rw["final"]["upstream_test_passes"] is True and rw["final"]["sources"]
    assert set(d["prices"]) == {"nano", "super", "ultra"}


def test_logo_is_served_and_is_a_valid_svg():
    r = client.get("/static/logo.svg")
    assert r.status_code == 200 and "<svg" in r.text and "Patchwright" in r.text


def test_pages_and_scripts_are_revalidated_so_updates_are_never_hidden_by_a_stale_cache():
    for path in ("/", "/static/app.js", "/static/pages.js", "/static/styles.css", "/static/logo.svg"):
        assert client.get(path).headers.get("cache-control") == "no-cache", path
    assert "cache-control" not in client.get("/api/config").headers or client.get("/api/config").headers["cache-control"] != "no-cache"


def test_every_nav_link_has_a_route_and_the_page_loads_both_scripts():
    import re
    from pathlib import Path
    static = Path(webapp.STATIC)
    html = (static / "index.html").read_text(encoding="utf-8")
    app_js, pages_js = (static / "app.js").read_text(encoding="utf-8"), (static / "pages.js").read_text(encoding="utf-8")
    assert '/static/app.js' in html and '/static/pages.js' in html and '/static/logo.svg' in html
    routes = {"home": "pageHome", "fix": "pageRun", "results": "pageResults", "how": "pageHow", "monitor": "pageMonitor", "history": "pageHistory"}
    assert set(re.findall(r'data-nav="(\w+)"', html)) == set(routes), "the navigation and the router must list the same pages"
    for fn in routes.values():
        assert f"function {fn}(" in app_js + pages_js, f"{fn} is missing"
    assert "pageHow" not in app_js.split("function pageHow")[0] or "function pageHow(" not in app_js, "pageHow must live in exactly one file"
