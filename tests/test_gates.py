from patchwright.gates import changed_ranges, detect_tools, on_changed_lines

DIFF = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -10,2 +10,3 @@ def f():
+x
@@ -40 +41,0 @@ def g():
-gone
diff --git a/tests/test_new.py b/tests/test_new.py
new file mode 100644
--- /dev/null
+++ b/tests/test_new.py
@@ -0,0 +1,20 @@
+all new
"""


def test_changed_ranges_ignores_pure_deletions():
    r = changed_ranges(DIFF)
    assert r["src/a.py"] == [(10, 12)]
    assert r["tests/test_new.py"] == [(1, 20)]


def test_only_problems_on_changed_lines_are_reported():
    r = changed_ranges(DIFF)
    out = "/work/src/a.py:11:5 - error: bad\nsrc/a.py:300:1: E501 old problem\ntests/test_new.py:3:1: I001 new\nsummary"
    assert on_changed_lines(out, r) == ["/work/src/a.py:11:5 - error: bad", "tests/test_new.py:3:1: I001 new"]


def test_detect_tools_from_config(tmp_path):
    (tmp_path / "tox.ini").write_text("[testenv:ruff]\ncommands = ruff check\n[testenv:pyright]\ncommands = pyright\n")
    (tmp_path / "pyproject.toml").write_text("[tool.black]\n[tool.ruff]\n")
    assert detect_tools(tmp_path) == ["ruff", "pyright"]  # black is dropped when ruff is present


def test_no_config_means_no_gates(tmp_path):
    assert detect_tools(tmp_path) == []
