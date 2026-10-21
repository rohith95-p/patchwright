"""Tests that need no model or network. Sandbox tests need Docker and are skipped without it."""
import shutil
import subprocess

import pytest

from patchwright import config
from patchwright.agent import _parse_action
from patchwright.doctor import pick_models

HAS_DOCKER = shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def test_parse_single_action():
    assert _parse_action('{"action":"run","cmd":"ls"}') == {"action": "run", "cmd": "ls"}


def test_parse_takes_first_of_many_and_strips_think():
    text = '<think>hmm {"action":"x"}</think>{"action":"run","cmd":"a"}{"action":"run","cmd":"b"}'
    assert _parse_action(text)["cmd"] == "a"


def test_parse_ignores_json_without_action_and_garbage():
    assert _parse_action('{"foo": 1} nonsense') is None
    assert _parse_action("no json here") is None


def test_parse_json_inside_prose_and_code_fence():
    assert _parse_action('Sure!\n```json\n{"action":"search","query":"q"}\n```')["query"] == "q"


def test_pick_models_prefers_native_nemotron3_and_skips_non_chat():
    ids = ["nvidia/nemotron-3-embed-1b", "nvidia/nemotron-3-nano-30b-a3b", "nvidia/nemotron-3-super-120b-a12b",
           "nvidia/nemotron-3-ultra-550b-a55b", "nvidia/llama-3.1-nemotron-ultra-253b-v1", "nvidia/nemotron-3.5-content-safety",
           "nvidia/nemotron-parse", "nvidia/nemotron-3.5-super-vl-120b-a12b", "meta/llama-3-8b"]
    assert pick_models(ids) == {"nano": "nvidia/nemotron-3-nano-30b-a3b", "super": "nvidia/nemotron-3-super-120b-a12b",
                                "ultra": "nvidia/nemotron-3-ultra-550b-a55b"}


def test_pick_models_falls_back_to_llama_ultra_when_no_native():
    assert pick_models(["nvidia/Llama-3_1-Nemotron-Ultra-253B-v1"]) == {"ultra": "nvidia/Llama-3_1-Nemotron-Ultra-253B-v1"}


def test_pick_models_handles_empty():
    assert pick_models([]) == {}


def test_base_url_guard_replaces_pasted_key(monkeypatch):
    monkeypatch.setenv("PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    monkeypatch.setenv("NVIDIA_BASE_URL", "nvapi-notaurl")
    assert config.get_provider().base_url == config.DEFAULT_URLS["nvidia"]


def test_provider_specific_model_wins(monkeypatch):
    monkeypatch.setenv("PROVIDER", "nebius")
    monkeypatch.setenv("SUPER_MODEL", "shared")
    monkeypatch.setenv("NEBIUS_SUPER_MODEL", "specific")
    assert config.get_model("super") == "specific"


def test_prices_loaded_for_all_tiers():
    assert set(config.PRICES) == {"nano", "super", "ultra"}
    assert all(len(v) == 2 for v in config.PRICES.values())


@pytest.fixture
def toy_repo(tmp_path):
    d = tmp_path / "toy"
    d.mkdir()
    (d / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (d / "test_calc.py").write_text("from calc import add\n\ndef test_zero():\n    assert add(0, 0) == 0\n")
    for c in (["git", "init", "-q"], ["git", "add", "."], ["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(c, cwd=d, check=True, capture_output=True)
    return str(d)


@pytest.mark.skipif(not HAS_DOCKER, reason="Docker not available")
def test_proof_accepts_real_fix_and_rejects_fake_test(toy_repo):
    from patchwright.sandbox import Sandbox
    with Sandbox(toy_repo) as sb:
        sb.setup()
        sb.write("test_new.py", "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
        sb.write("calc.py", "def add(a, b):\n    return a + b\n")
        ok, _ = sb.prove_fails_without_fix("python -m pytest -q test_new.py")
        assert ok, "a test that fails without the fix must be accepted"
        assert sb.exec("python -m pytest -q test_new.py")[0] == 0, "fix must be restored after the proof"
        sb.write("test_fake.py", "def test_trivial():\n    assert True\n")
        ok, why = sb.prove_fails_without_fix("python -m pytest -q test_fake.py")
        assert not ok and "WITHOUT" in why, "a test that passes without the fix must be rejected"


@pytest.mark.skipif(not HAS_DOCKER, reason="Docker not available")
def test_proof_rejects_edited_existing_tests_and_no_source_change(toy_repo):
    from patchwright.sandbox import Sandbox
    with Sandbox(toy_repo) as sb:
        sb.setup()
        ok, why = sb.prove_fails_without_fix("true")
        assert not ok and "No change" in why
        sb.write("test_calc.py", "def test_zero():\n    assert True\n")
        ok, why = sb.prove_fails_without_fix("true")
        assert not ok and "existing test" in why


@pytest.mark.skipif(not HAS_DOCKER, reason="Docker not available")
def test_write_cannot_escape_repo_and_reset_restores(toy_repo):
    from patchwright.sandbox import Sandbox
    with Sandbox(toy_repo) as sb:
        with pytest.raises(ValueError):
            sb.write("../../evil.txt", "x")
        sb.write("calc.py", "BROKEN")
        sb.reset()
        assert "a - b" in sb.exec("cat calc.py")[1]


@pytest.mark.skipif(not HAS_DOCKER, reason="Docker not available")
def test_sandbox_survives_undecodable_output(toy_repo):
    from patchwright.sandbox import Sandbox
    with Sandbox(toy_repo) as sb:
        code, out = sb.exec("printf 'caf\xe9 \xff\xfe ok'")
        assert isinstance(out, str) and "ok" in out
