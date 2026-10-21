"""patchwright.context: find the code an issue names. Pure text search, no Docker, no model."""
from patchwright.context import identifiers, relevant_excerpts

ISSUE = """RRCache.popitem() on an empty cache raises the wrong exception

The other classes (`FIFOCache`, `LRUCache`) raise KeyError correctly; `RRCache(choice=min)` does not.
"""
SOURCE = '''class FIFOCache:
    def popitem(self):
        try:
            return next(iter(self.order))
        except StopIteration:
            raise KeyError("empty")


class RRCache:
    """Random replacement."""

    def popitem(self):
        try:
            key = self.choice(self.keys)
        except IndexError:
            raise KeyError("empty")
        return key
'''


def make(tmp_path):
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "__init__.py").write_text(SOURCE)
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_x.py").write_text("class RRCache:\n    def popitem(self):\n        pass\n")
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "conf.py").write_text("class RRCache:\n    def popitem(self):\n        pass\n")
    return tmp_path


def test_title_names_outweigh_names_only_in_the_body():
    w = identifiers(ISSUE)
    assert w["RRCache"] == 3 and w["popitem"] == 3 and w["FIFOCache"] == 1 and w["LRUCache"] == 1
    assert "this" not in w and "with" not in w, "stop words and short words are dropped"


def test_the_class_in_the_title_beats_a_class_mentioned_for_comparison(tmp_path):
    ex = relevant_excerpts(make(tmp_path), ISSUE)
    first = ex.split("--- ")[1]
    assert "pkg/__init__.py" in first and "except IndexError" in ex
    assert ex.index("self.choice(self.keys)") < (ex.index("next(iter(self.order))") if "next(iter(self.order))" in ex else 10**9), \
        "RRCache.popitem must come before FIFOCache.popitem"


def test_tests_and_docs_directories_are_never_excerpted(tmp_path):
    ex = relevant_excerpts(make(tmp_path), ISSUE)
    assert "tests/" not in ex and "docs/" not in ex


def test_no_names_or_no_matches_gives_nothing(tmp_path):
    assert relevant_excerpts(make(tmp_path), "it is broken") == ""
    assert relevant_excerpts(make(tmp_path), "`NothingLikeThis` fails") == ""


def test_excerpt_size_is_bounded(tmp_path):
    (tmp_path / "big.py").write_text("class RRCache:\n    def popitem(self):\n" + "        x = 1\n" * 500)
    assert len(relevant_excerpts(tmp_path, ISSUE, max_chars=800)) <= 900
