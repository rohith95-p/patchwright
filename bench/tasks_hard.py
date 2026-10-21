"""Harder Patchwright-Bench tasks (difficulty 3-4): subtler logic, more code, several valid-looking wrong fixes.

Each task carries a `reference` fix used ONLY by bench/validate.py to prove the hidden test is fair
(it passes on a correct fix and fails on the buggy code). The agent never sees `reference` or `hidden`.
"""

EXPR = '''import re


def tokenize(s):
    return re.findall(r"\\d+(?:\\.\\d+)?|[()+\\-*/^]", s)


class Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def eat(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expr(self):
        v = self.term()
        while self.peek() in ("+", "-"):
            op = self.eat()
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self):
        v = self.power()
        while self.peek() in ("*", "/"):
            op = self.eat()
            r = self.power()
            v = v * r if op == "*" else v / r
        return v

    def power(self):
        v = self.atom()
        while self.peek() == "^":
            self.eat()
            v = v ** self.atom()
        return v

    def atom(self):
        tok = self.eat()
        if tok == "(":
            v = self.expr()
            self.eat()
            return v
        if tok == "-":
            return -self.atom()
        return float(tok)


def evaluate(s):
    return Parser(tokenize(s)).expr()
'''

EXPR_FIXED = EXPR.replace('''    def power(self):
        v = self.atom()
        while self.peek() == "^":
            self.eat()
            v = v ** self.atom()
        return v

    def atom(self):
        tok = self.eat()
        if tok == "(":
            v = self.expr()
            self.eat()
            return v
        if tok == "-":
            return -self.atom()
        return float(tok)''', '''    def power(self):
        if self.peek() == "-":
            self.eat()
            return -self.power()
        base = self.atom()
        if self.peek() == "^":
            self.eat()
            return base ** self.power()
        return base

    def atom(self):
        tok = self.eat()
        if tok == "(":
            v = self.expr()
            self.eat()
            return v
        return float(tok)''')

HARD = [
    {
        "id": "token-bucket", "difficulty": 3,
        "issue": "My rate limiter lets far too many requests through over time. With capacity 2 and 1 token per second, once the bucket is empty I can still get a request allowed roughly every tenth of a second.",
        "files": {
            "rate.py": "class TokenBucket:\n    def __init__(self, capacity, refill_per_sec, now=0.0):\n        self.capacity = capacity\n        self.rate = refill_per_sec\n        self.tokens = capacity\n        self.last = now\n\n    def allow(self, now, cost=1):\n        elapsed = now - self.last\n        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)\n        if self.tokens >= cost:\n            self.tokens -= cost\n            return True\n        return False\n",
            "test_visible.py": "from rate import TokenBucket\n\ndef test_burst_then_deny():\n    b = TokenBucket(2, 1.0)\n    assert b.allow(0) and b.allow(0)\n    assert not b.allow(0)\n",
        },
        "hidden": {"test_hidden.py": "from rate import TokenBucket\n\ndef test_refill_not_double_counted():\n    b = TokenBucket(2, 1.0)\n    assert b.allow(0) and b.allow(0)\n    assert not b.allow(0.5)\n    assert b.allow(1.0)\n    assert not b.allow(1.1)\n    assert not b.allow(1.9)\n    assert b.allow(2.0)\n\ndef test_cap():\n    b = TokenBucket(2, 1.0)\n    assert b.allow(100) and b.allow(100)\n    assert not b.allow(100)\n"},
        "reference": {"rate.py": "class TokenBucket:\n    def __init__(self, capacity, refill_per_sec, now=0.0):\n        self.capacity = capacity\n        self.rate = refill_per_sec\n        self.tokens = capacity\n        self.last = now\n\n    def allow(self, now, cost=1):\n        elapsed = now - self.last\n        self.last = now\n        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)\n        if self.tokens >= cost:\n            self.tokens -= cost\n            return True\n        return False\n"},
    },
    {
        "id": "interval-touching", "difficulty": 3,
        "issue": "merge([(1, 3), (3, 5)]) should give [(1, 5)] because the ranges share the endpoint 3, but I get two separate ranges back.",
        "files": {
            "intervals.py": "def merge(intervals):\n    out = []\n    for s, e in sorted(intervals):\n        if out and s < out[-1][1]:\n            out[-1][1] = max(out[-1][1], e)\n        else:\n            out.append([s, e])\n    return [tuple(x) for x in out]\n",
            "test_visible.py": "from intervals import merge\n\ndef test_disjoint_and_overlap():\n    assert merge([(1, 2), (5, 6)]) == [(1, 2), (5, 6)]\n    assert merge([(1, 4), (2, 5)]) == [(1, 5)]\n",
        },
        "hidden": {"test_hidden.py": "from intervals import merge\n\ndef test_touching():\n    assert merge([(1, 3), (3, 5)]) == [(1, 5)]\n    assert merge([(5, 8), (1, 3), (3, 5)]) == [(1, 8)]\n\ndef test_contained_and_empty():\n    assert merge([(1, 10), (2, 3)]) == [(1, 10)]\n    assert merge([]) == []\n"},
        "reference": {"intervals.py": "def merge(intervals):\n    out = []\n    for s, e in sorted(intervals):\n        if out and s <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], e)\n        else:\n            out.append([s, e])\n    return [tuple(x) for x in out]\n"},
    },
    {
        "id": "semver-compare", "difficulty": 3,
        "issue": "compare('1.10.0', '1.9.0') says 1.10.0 is OLDER. Also compare('1.2', '1.2.0') should be 0 (equal) but it is not. compare returns -1, 0 or 1.",
        "files": {
            "semver.py": "def parse(v):\n    return tuple(int(x) for x in v.split('.'))\n\n\ndef compare(a, b):\n    return (a > b) - (a < b)\n",
            "test_visible.py": "from semver import compare, parse\n\ndef test_basic():\n    assert compare('1.0.0', '2.0.0') == -1\n    assert compare('2.0.0', '2.0.0') == 0\n    assert parse('1.2.3') == (1, 2, 3)\n",
        },
        "hidden": {"test_hidden.py": "from semver import compare\n\ndef test_numeric():\n    assert compare('1.10.0', '1.9.0') == 1\n    assert compare('1.9.0', '1.10.0') == -1\n    assert compare('2.0', '10.0') == -1\n\ndef test_padding():\n    assert compare('1.2', '1.2.0') == 0\n    assert compare('1.2', '1.2.1') == -1\n    assert compare('1.3', '1.2.9') == 1\n"},
        "reference": {"semver.py": "def parse(v):\n    return tuple(int(x) for x in v.split('.'))\n\n\ndef compare(a, b):\n    pa, pb = list(parse(a)), list(parse(b))\n    n = max(len(pa), len(pb))\n    pa += [0] * (n - len(pa))\n    pb += [0] * (n - len(pb))\n    return (pa > pb) - (pa < pb)\n"},
    },
    {
        "id": "expr-precedence", "difficulty": 4,
        "issue": "The calculator gets exponents wrong. 2^3^2 should be 512 because ^ is right-associative (2^(3^2)), but I get 64. Also -2^2 should be -4 (the minus applies after the power), but I get 4.",
        "files": {
            "calc.py": EXPR,
            "test_visible.py": "from calc import evaluate\n\ndef test_basic():\n    assert evaluate('1+2*3') == 7\n    assert evaluate('(1+2)*3') == 9\n    assert evaluate('2^3') == 8\n    assert evaluate('10/4') == 2.5\n",
        },
        "hidden": {"test_hidden.py": "from calc import evaluate\n\ndef test_right_assoc():\n    assert evaluate('2^3^2') == 512\n    assert evaluate('2^(3^2)') == 512\n    assert evaluate('(2^3)^2') == 64\n\ndef test_unary_minus():\n    assert evaluate('-2^2') == -4\n    assert evaluate('(-2)^2') == 4\n    assert evaluate('2*-3') == -6\n    assert evaluate('3-2^2') == -1\n    assert evaluate('2^-1') == 0.5\n"},
        "reference": {"calc.py": EXPR_FIXED},
    },
    {
        "id": "text-wrap-boundary", "difficulty": 3,
        "issue": "wrap('aaa bbb', 7) should return ['aaa bbb'] because the line is exactly 7 characters and width is 7, but it splits into two lines. Lines up to and including the width should be allowed.",
        "files": {
            "wrap.py": "def wrap(text, width):\n    lines, cur = [], ''\n    for word in text.split():\n        if not cur:\n            cur = word\n        elif len(cur) + 1 + len(word) < width:\n            cur += ' ' + word\n        else:\n            lines.append(cur)\n            cur = word\n    if cur:\n        lines.append(cur)\n    return lines\n",
            "test_visible.py": "from wrap import wrap\n\ndef test_basic():\n    assert wrap('a b c', 100) == ['a b c']\n    assert wrap('', 5) == []\n",
        },
        "hidden": {"test_hidden.py": "from wrap import wrap\n\ndef test_exact_fit():\n    assert wrap('aaa bbb', 7) == ['aaa bbb']\n    assert wrap('aaa bbb ccc', 7) == ['aaa bbb', 'ccc']\n    assert wrap('aaa bbb ccc', 11) == ['aaa bbb ccc']\n\ndef test_long_word():\n    assert wrap('abcdefghij kl', 5) == ['abcdefghij', 'kl']\n"},
        "reference": {"wrap.py": "def wrap(text, width):\n    lines, cur = [], ''\n    for word in text.split():\n        if not cur:\n            cur = word\n        elif len(cur) + 1 + len(word) <= width:\n            cur += ' ' + word\n        else:\n            lines.append(cur)\n            cur = word\n    if cur:\n        lines.append(cur)\n    return lines\n"},
    },
    {
        "id": "split-cents", "difficulty": 3,
        "issue": "Splitting a 100 cent bill three ways gives [33, 33, 33] which adds up to 99. The shares must always add up exactly to the total, with the leftover cents given one each to the first people. Also splitting among 0 people should raise ValueError.",
        "files": {
            "bill.py": "def split_cents(total_cents, n):\n    base = total_cents // n\n    return [base] * n\n",
            "test_visible.py": "from bill import split_cents\n\ndef test_even():\n    assert split_cents(90, 3) == [30, 30, 30]\n    assert split_cents(5, 5) == [1, 1, 1, 1, 1]\n",
        },
        "hidden": {"test_hidden.py": "import pytest\nfrom bill import split_cents\n\ndef test_remainder():\n    assert split_cents(100, 3) == [34, 33, 33]\n    assert split_cents(7, 4) == [2, 2, 2, 1]\n    assert split_cents(1, 3) == [1, 0, 0]\n\ndef test_sum_always_total():\n    for total in range(0, 40):\n        for n in range(1, 8):\n            assert sum(split_cents(total, n)) == total\n\ndef test_zero_people():\n    with pytest.raises(ValueError):\n        split_cents(10, 0)\n"},
        "reference": {"bill.py": "def split_cents(total_cents, n):\n    if n <= 0:\n        raise ValueError('n must be positive')\n    base, extra = divmod(total_cents, n)\n    return [base + 1 if i < extra else base for i in range(n)]\n"},
    },
    {
        "id": "ttl-boundary", "difficulty": 3,
        "issue": "My TTLCache entry set at time 0 with ttl=10 should be expired at exactly time 10, but get() still returns the value at t=10 and only drops it after that.",
        "files": {
            "ttl.py": "class TTLCache:\n    def __init__(self, ttl, clock):\n        self.ttl = ttl\n        self.clock = clock\n        self.data = {}\n\n    def set(self, k, v):\n        self.data[k] = (v, self.clock())\n\n    def get(self, k):\n        item = self.data.get(k)\n        if item is None:\n            return None\n        v, t = item\n        if self.clock() - t > self.ttl:\n            del self.data[k]\n            return None\n        return v\n",
            "test_visible.py": "from ttl import TTLCache\n\ndef test_basic():\n    now = [0]\n    c = TTLCache(10, lambda: now[0])\n    c.set('a', 1)\n    now[0] = 5\n    assert c.get('a') == 1\n    now[0] = 20\n    assert c.get('a') is None\n",
        },
        "hidden": {"test_hidden.py": "from ttl import TTLCache\n\ndef test_boundary():\n    now = [0]\n    c = TTLCache(10, lambda: now[0])\n    c.set('a', 1)\n    now[0] = 9.99\n    assert c.get('a') == 1\n    now[0] = 10\n    assert c.get('a') is None\n\ndef test_reset_on_set():\n    now = [0]\n    c = TTLCache(10, lambda: now[0])\n    c.set('a', 1)\n    now[0] = 8\n    c.set('a', 2)\n    now[0] = 17\n    assert c.get('a') == 2\n    now[0] = 18\n    assert c.get('a') is None\n"},
        "reference": {"ttl.py": "class TTLCache:\n    def __init__(self, ttl, clock):\n        self.ttl = ttl\n        self.clock = clock\n        self.data = {}\n\n    def set(self, k, v):\n        self.data[k] = (v, self.clock())\n\n    def get(self, k):\n        item = self.data.get(k)\n        if item is None:\n            return None\n        v, t = item\n        if self.clock() - t >= self.ttl:\n            del self.data[k]\n            return None\n        return v\n"},
    },
    {
        "id": "toposort-cycle", "difficulty": 3,
        "issue": "toposort({'a': ['b'], 'b': ['a']}) quietly returns ['b', 'a'] even though a and b depend on each other. Circular dependencies should raise ValueError. A node that depends on itself is also a cycle.",
        "files": {
            "topo.py": "def toposort(deps):\n    order, seen = [], set()\n\n    def visit(n):\n        if n in seen:\n            return\n        seen.add(n)\n        for d in deps.get(n, []):\n            visit(d)\n        order.append(n)\n\n    for n in sorted(deps):\n        visit(n)\n    return order\n",
            "test_visible.py": "from topo import toposort\n\ndef test_chain():\n    assert toposort({'a': ['b'], 'b': ['c'], 'c': []}) == ['c', 'b', 'a']\n",
        },
        "hidden": {"test_hidden.py": "import pytest\nfrom topo import toposort\n\ndef test_cycle():\n    with pytest.raises(ValueError):\n        toposort({'a': ['b'], 'b': ['a']})\n    with pytest.raises(ValueError):\n        toposort({'a': ['a']})\n    with pytest.raises(ValueError):\n        toposort({'a': ['b'], 'b': ['c'], 'c': ['a']})\n\ndef test_diamond_and_unknown_dep():\n    out = toposort({'a': ['b', 'c'], 'b': ['d'], 'c': ['d'], 'd': []})\n    assert out.index('d') < out.index('b') < out.index('a')\n    assert out.index('d') < out.index('c') < out.index('a')\n    assert sorted(toposort({'a': ['x']})) == ['a', 'x']\n"},
        "reference": {"topo.py": "def toposort(deps):\n    order, done, active = [], set(), set()\n\n    def visit(n):\n        if n in done:\n            return\n        if n in active:\n            raise ValueError('cycle detected at ' + str(n))\n        active.add(n)\n        for d in deps.get(n, []):\n            visit(d)\n        active.discard(n)\n        done.add(n)\n        order.append(n)\n\n    for n in sorted(deps):\n        visit(n)\n    return order\n"},
    },
]
