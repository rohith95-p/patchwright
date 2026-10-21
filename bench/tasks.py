"""Patchwright-Bench: small repos with one injected, realistic bug each.

Every task has: visible tests (pass on the buggy code, so they do not reveal the bug), a user-style
issue report, and a hidden test the agent never sees. A fix is 'truly correct' only if the hidden
test passes too. Difficulty: 1 = one-line, 2 = needs reasoning, 3 = multi-file or subtle.
"""

TASKS = [
    {
        "id": "avg-offbyone", "difficulty": 1,
        "issue": "average() gives wrong numbers. average([2, 4, 6]) returns 6.0 but I expect 4.0.",
        "files": {
            "stats.py": "def average(nums):\n    return sum(nums) / (len(nums) - 1)\n",
            "test_visible.py": "from stats import average\n\ndef test_single_zero():\n    assert average([0, 0]) == 0\n",
        },
        "hidden": {"test_hidden.py": "from stats import average\n\ndef test_a():\n    assert average([2, 4, 6]) == 4\n    assert average([10]) == 10\n"},
    },
    {
        "id": "mutable-default", "difficulty": 2,
        "issue": "Calling add_tag('a') on one item and then add_tag('b') on a different new item shows both tags on the second item. Tags seem to leak between items.",
        "files": {
            "tags.py": "def add_tag(tag, tags=[]):\n    tags.append(tag)\n    return tags\n",
            "test_visible.py": "from tags import add_tag\n\ndef test_explicit():\n    assert add_tag('x', ['y']) == ['y', 'x']\n",
        },
        "hidden": {"test_hidden.py": "from tags import add_tag\n\ndef test_isolated():\n    assert add_tag('a') == ['a']\n    assert add_tag('b') == ['b']\n"},
    },
    {
        "id": "pagination", "difficulty": 2,
        "issue": "paginate(items, page, size): page 1 should return the first `size` items but the very first item is missing, and the last page is cut short.",
        "files": {
            "pager.py": "def paginate(items, page, size):\n    start = page * size\n    return items[start:start + size]\n",
            "test_visible.py": "from pager import paginate\n\ndef test_empty():\n    assert paginate([], 1, 5) == []\n",
        },
        "hidden": {"test_hidden.py": "from pager import paginate\n\ndef test_pages():\n    d = list(range(10))\n    assert paginate(d, 1, 4) == [0, 1, 2, 3]\n    assert paginate(d, 3, 4) == [8, 9]\n"},
    },
    {
        "id": "word-count-case", "difficulty": 1,
        "issue": "word_count('The the THE cat') should say the=3 and cat=1 but it treats 'The' and 'the' as different words.",
        "files": {
            "words.py": "def word_count(text):\n    counts = {}\n    for w in text.split():\n        counts[w] = counts.get(w, 0) + 1\n    return counts\n",
            "test_visible.py": "from words import word_count\n\ndef test_basic():\n    assert word_count('a b a') == {'a': 2, 'b': 1}\n",
        },
        "hidden": {"test_hidden.py": "from words import word_count\n\ndef test_case():\n    assert word_count('The the THE cat') == {'the': 3, 'cat': 1}\n"},
    },
    {
        "id": "median-even", "difficulty": 2,
        "issue": "median([1, 2, 3, 4]) should be 2.5 but returns 3.",
        "files": {
            "m.py": "def median(nums):\n    s = sorted(nums)\n    return s[len(s) // 2]\n",
            "test_visible.py": "from m import median\n\ndef test_odd():\n    assert median([3, 1, 2]) == 2\n",
        },
        "hidden": {"test_hidden.py": "from m import median\n\ndef test_even():\n    assert median([1, 2, 3, 4]) == 2.5\n    assert median([5, 1]) == 3\n    assert median([7]) == 7\n"},
    },
    {
        "id": "merge-deep", "difficulty": 3,
        "issue": "merge_config(defaults, overrides) wipes out nested settings. If defaults has {'db': {'host': 'a', 'port': 1}} and overrides has {'db': {'port': 2}}, db.host disappears.",
        "files": {
            "cfg.py": "def merge_config(defaults, overrides):\n    out = dict(defaults)\n    out.update(overrides)\n    return out\n",
            "test_visible.py": "from cfg import merge_config\n\ndef test_flat():\n    assert merge_config({'a': 1}, {'b': 2}) == {'a': 1, 'b': 2}\n",
        },
        "hidden": {"test_hidden.py": "from cfg import merge_config\n\ndef test_nested():\n    d = {'db': {'host': 'a', 'port': 1}, 'x': 1}\n    o = {'db': {'port': 2}}\n    assert merge_config(d, o) == {'db': {'host': 'a', 'port': 2}, 'x': 1}\n    assert d == {'db': {'host': 'a', 'port': 1}, 'x': 1}\n"},
    },
    {
        "id": "slugify-unicode", "difficulty": 2,
        "issue": "slugify('  Hello,   World!  ') returns 'hello,---world!' . I want 'hello-world': only lowercase letters, digits and single hyphens, no leading or trailing hyphen.",
        "files": {
            "slug.py": "def slugify(s):\n    return s.strip().lower().replace(' ', '-')\n",
            "test_visible.py": "from slug import slugify\n\ndef test_simple():\n    assert slugify('Hello World') == 'hello-world'\n",
        },
        "hidden": {"test_hidden.py": "from slug import slugify\n\ndef test_clean():\n    assert slugify('  Hello,   World!  ') == 'hello-world'\n    assert slugify('A--B') == 'a-b'\n    assert slugify('!!!') == ''\n"},
    },
    {
        "id": "lru-eviction", "difficulty": 3,
        "issue": "My LRU cache evicts the wrong key. After get('a') the key 'a' should count as recently used, but it is still the first one evicted when the cache is full.",
        "files": {
            "lru.py": "class LRU:\n    def __init__(self, cap):\n        self.cap = cap\n        self.d = {}\n\n    def get(self, k):\n        return self.d.get(k)\n\n    def put(self, k, v):\n        if k in self.d:\n            del self.d[k]\n        elif len(self.d) >= self.cap:\n            del self.d[next(iter(self.d))]\n        self.d[k] = v\n",
            "test_visible.py": "from lru import LRU\n\ndef test_basic():\n    c = LRU(2)\n    c.put('a', 1)\n    assert c.get('a') == 1\n    assert c.get('z') is None\n",
        },
        "hidden": {"test_hidden.py": "from lru import LRU\n\ndef test_recent():\n    c = LRU(2)\n    c.put('a', 1)\n    c.put('b', 2)\n    c.get('a')\n    c.put('c', 3)\n    assert c.get('b') is None\n    assert c.get('a') == 1\n    assert c.get('c') == 3\n"},
    },
    {
        "id": "date-range-inclusive", "difficulty": 2,
        "issue": "days_between('2024-03-01', '2024-03-05') should list 5 days including both end dates, but the last day is missing.",
        "files": {
            "dates.py": "from datetime import date, timedelta\n\ndef days_between(a, b):\n    d0, d1 = date.fromisoformat(a), date.fromisoformat(b)\n    out = []\n    cur = d0\n    while cur < d1:\n        out.append(cur.isoformat())\n        cur += timedelta(days=1)\n    return out\n",
            "test_visible.py": "from dates import days_between\n\ndef test_reversed_empty():\n    assert days_between('2024-03-05', '2024-03-01') == []\n",
        },
        "hidden": {"test_hidden.py": "from dates import days_between\n\ndef test_inclusive():\n    assert days_between('2024-03-01', '2024-03-05') == ['2024-03-01','2024-03-02','2024-03-03','2024-03-04','2024-03-05']\n    assert days_between('2024-03-01', '2024-03-01') == ['2024-03-01']\n"},
    },
    {
        "id": "csv-quoted", "difficulty": 3,
        "issue": "parse_line('a,\"b,c\",d') should give ['a', 'b,c', 'd'] but I get 4 fields because the comma inside quotes splits the value.",
        "files": {
            "csvp.py": "def parse_line(line):\n    return line.split(',')\n",
            "test_visible.py": "from csvp import parse_line\n\ndef test_plain():\n    assert parse_line('a,b,c') == ['a', 'b', 'c']\n",
        },
        "hidden": {"test_hidden.py": "from csvp import parse_line\n\ndef test_quoted():\n    assert parse_line('a,\"b,c\",d') == ['a', 'b,c', 'd']\n    assert parse_line('\"x\",y') == ['x', 'y']\n    assert parse_line('') == ['']\n"},
    },
    {
        "id": "discount-rounding", "difficulty": 2,
        "issue": "price_after_discount(19.99, 15) gives 16.9915000000001 on my invoice. Prices must be rounded to 2 decimals (half up, so 16.99), and discount over 100 percent should cap at 0 price, not go negative.",
        "files": {
            "price.py": "def price_after_discount(price, pct):\n    return price * (1 - pct / 100)\n",
            "test_visible.py": "from price import price_after_discount\n\ndef test_zero():\n    assert price_after_discount(10, 0) == 10\n",
        },
        "hidden": {"test_hidden.py": "from price import price_after_discount\n\ndef test_round():\n    assert price_after_discount(19.99, 15) == 16.99\n    assert price_after_discount(10, 150) == 0\n    assert price_after_discount(0.5, 50) == 0.25\n"},
    },
    {
        "id": "stack-two-files", "difficulty": 3,
        "issue": "Both the report totals and the invoice totals are off whenever an item has quantity > 1. For example an item at 5.0 x 3 should count as 15.0.",
        "files": {
            "core.py": "def line_total(item):\n    return item['price']\n",
            "report.py": "from core import line_total\n\ndef report_total(items):\n    return sum(line_total(i) for i in items)\n",
            "invoice.py": "from core import line_total\n\ndef invoice_text(items):\n    return f\"TOTAL {sum(line_total(i) for i in items):.2f}\"\n",
            "test_visible.py": "from report import report_total\n\ndef test_single_qty():\n    assert report_total([{'price': 5.0, 'qty': 1}]) == 5.0\n",
        },
        "hidden": {"test_hidden.py": "from report import report_total\nfrom invoice import invoice_text\n\ndef test_qty():\n    items = [{'price': 5.0, 'qty': 3}, {'price': 1.0, 'qty': 2}]\n    assert report_total(items) == 17.0\n    assert invoice_text(items) == 'TOTAL 17.00'\n"},
    },
]


from .tasks_hard import HARD  # noqa: E402

TASKS += HARD
