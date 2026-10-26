"""Regular path expressions: what a ``regular_path`` request asks a walk to match, and the automaton that matches it.

A path expression describes walks by their hops, as a regular expression whose letters are steps. A **step** matches
one hop: an edge in its scope (with the relation hierarchy, as every scope), followed in its direction, landing on a
node of its node types (any node when null). Expressions combine steps in sequence, in alternation and in repetition,
as regular path queries do (Mendelzon and Wood, 1995) and as SPARQL 1.1 property paths and GQL path patterns write
them. The normalized form is a JSON tree, and every node of it has an ``op``:

* ``{"op": "step", "scope": [{relation, direction}], "node_types": [...] | null}``
* ``{"op": "seq", "items": [e, e, ...]}``: one after another (nested sequences are flattened)
* ``{"op": "alt", "items": [e, e, ...]}``: any one (flattened, de-duplicated and sorted by canonical JSON, so that
  ``A|B`` and ``B|A`` are one request)
* ``{"op": "repeat", "item": e, "min": m, "max": n | null}``: m to n times (null: no upper bound); ``*`` is
  ``{0, null}``, ``+`` is ``{1, null}`` and ``?`` is ``{0, 1}``

A request may give the tree, a list (a sequence) or the text syntax, which normalizes to the tree; text may also
stand for any part of a tree::

    path   := alt
    alt    := seq ("|" seq)*
    seq    := post ("/" post)*
    post   := atom ("*" | "+" | "?" | "{" m "}" | "{" m "," "}" | "{" m "," n "}")*
    atom   := "(" alt ")" | "^"? name ("[" name ("," name)* "]")?
    name   := letters, digits and _ . : - ; or any text between backquotes

``CITES+/^SUPPORTS[Claim]`` reads: one or more CITES hops forward, then one SUPPORTS hop in reverse landing on a
Claim. ``^`` follows a relation in reverse; ``[...]`` lists the node types a step lands on.

**The automaton.** :func:`automaton` compiles an expression to its Glushkov (position) automaton, which has no empty
moves: state 0 is the start, and state i (1 to the number of steps) is "the i-th step was the last hop matched".
Steps are numbered in the order they are written once bounded repetitions are written out: ``e{m,n}`` is m copies of
``e`` then n - m optional copies, ``e{m,}`` (m > 0) is m - 1 copies then ``e+``, and ``e{0,}`` is ``e*``. ``follow[q]``
lists the states a hop can move to from state q, and a walk matches when it ends in an accepting state (a last step,
or the start when the expression matches the empty walk). States are part of answers and certificates, so this
numbering is part of the contract.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, NamedTuple

from ._canonical import canonical_json

MAX_STEPS = 4096                       # steps of an expression with its bounded repetitions written out
OPS = ("step", "seq", "alt", "repeat")


class PathError(ValueError):
    """A path expression that cannot be read; ``where`` is the text offset for the text syntax."""

    def __init__(self, message: str, where: int | None = None):
        super().__init__(message if where is None else f"{message} (at character {where})")
        self.where = where


# ---------------------------------------------------------------------------------------------- text syntax
_TOKEN = re.compile(r"\s*(?:(?P<name>[A-Za-z0-9_.:\-]+)|`(?P<quoted>[^`]+)`|(?P<punct>[()|/*+?^\[\],{}]))")


def _tokens(text: str) -> list[tuple[str, str, int]]:
    out, i = [], 0
    while i < len(text):
        if text[i:].strip() == "":
            break
        m = _TOKEN.match(text, i)
        if m is None:
            raise PathError(f"unexpected {text[i:].lstrip()[:1]!r} in the path expression", i)
        where = m.start(m.lastgroup)
        if m.lastgroup == "punct":
            out.append(("punct", m.group("punct"), where))
        else:
            out.append(("name", m.group(m.lastgroup), where))
        i = m.end()
    out.append(("end", "", len(text)))
    return out


class _Parser:
    def __init__(self, text: str):
        self.toks, self.i = _tokens(text), 0

    def peek(self) -> tuple[str, str, int]:
        return self.toks[self.i]

    def take(self, value: str | None = None, kind: str | None = None) -> tuple[str, str, int]:
        tok = self.toks[self.i]
        if (value is not None and (tok[0] != "punct" or tok[1] != value)) or (kind is not None and tok[0] != kind):
            want = repr(value) if value is not None else "a name"
            got = "the end" if tok[0] == "end" else repr(tok[1])
            raise PathError(f"expected {want}, found {got}", tok[2])
        self.i += 1
        return tok

    def at(self, value: str) -> bool:
        tok = self.toks[self.i]
        return tok[0] == "punct" and tok[1] == value

    def path(self) -> dict:
        e = self.alt()
        if self.peek()[0] != "end":
            raise PathError(f"unexpected {self.peek()[1]!r}", self.peek()[2])
        return e

    def alt(self) -> dict:
        items = [self.seq()]
        while self.at("|"):
            self.take("|")
            items.append(self.seq())
        return items[0] if len(items) == 1 else {"op": "alt", "items": items}

    def seq(self) -> dict:
        items = [self.post()]
        while self.at("/"):
            self.take("/")
            items.append(self.post())
        return items[0] if len(items) == 1 else {"op": "seq", "items": items}

    def post(self) -> dict:
        e = self.atom()
        while True:
            if self.at("*"):
                self.take("*")
                e = {"op": "repeat", "item": e, "min": 0, "max": None}
            elif self.at("+"):
                self.take("+")
                e = {"op": "repeat", "item": e, "min": 1, "max": None}
            elif self.at("?"):
                self.take("?")
                e = {"op": "repeat", "item": e, "min": 0, "max": 1}
            elif self.at("{"):
                self.take("{")
                lo = self.count()
                hi: int | None = lo
                if self.at(","):
                    self.take(",")
                    hi = None if self.at("}") else self.count()
                self.take("}")
                e = {"op": "repeat", "item": e, "min": lo, "max": hi}
            else:
                return e

    def count(self) -> int:
        tok = self.take(kind="name")
        if not tok[1].isdigit() or not tok[1].isascii():
            raise PathError(f"a repetition count is a non-negative integer, not {tok[1]!r}", tok[2])
        return int(tok[1])

    def atom(self) -> dict:
        if self.at("("):
            self.take("(")
            e = self.alt()
            self.take(")")
            return e
        direction = "forward"
        if self.at("^"):
            self.take("^")
            direction = "reverse"
        relation = self.take(kind="name")[1]
        types = None
        if self.at("["):
            self.take("[")
            types = [self.take(kind="name")[1]]
            while self.at(","):
                self.take(",")
                types.append(self.take(kind="name")[1])
            self.take("]")
        return {"op": "step", "scope": [{"relation": relation, "direction": direction}], "node_types": types}


def parse(text: str) -> dict:
    """The tree of a path expression in the text syntax (not yet normalized)."""
    if not isinstance(text, str) or not text.strip():
        raise PathError("a path expression in text is a non-empty string")
    return _Parser(text).path()


# ---------------------------------------------------------------------------------------------- normal form
def normalize(value: Any, scope: Any, types: Any) -> dict:
    """The normal form of a path expression given as text, a list (a sequence) or a tree. ``scope`` and ``types``
    normalize a step's scope and node types (the request module's own checks), so that a step reads as every
    operation's scope does. Raises :class:`PathError`."""
    if isinstance(value, str):
        value = parse(value)
    tree = _normal(value, scope, types, "path")
    n = steps_written_out(tree)
    if n > MAX_STEPS:
        raise PathError(f"the expression has {n} steps once its bounded repetitions are written out; at most "
                        f"{MAX_STEPS}")
    return tree


def _normal(e: Any, scope: Any, types: Any, where: str) -> dict:
    if isinstance(e, str):                # text may stand for any part of a tree
        e = parse(e)
    if isinstance(e, (list, tuple)):
        e = {"op": "seq", "items": list(e)}
    if not isinstance(e, Mapping):
        raise PathError(f"{where} is an expression (text, a list or a mapping with op), not {e!r}")
    op = e.get("op")
    allowed = {"step": ("op", "scope", "node_types"), "seq": ("op", "items"), "alt": ("op", "items"),
               "repeat": ("op", "item", "min", "max")}.get(op)
    if allowed is None:
        raise PathError(f"{where}.op is one of {list(OPS)}, not {op!r}")
    stray = sorted(str(k) for k in e if k not in allowed)
    if stray:
        raise PathError(f"{where}: a {op} takes {list(allowed[1:])}, not {stray}")
    if op == "step":
        if e.get("scope") is None:
            raise PathError(f"{where}: a step needs a scope")
        return {"op": "step", "scope": scope(e["scope"], f"{where}.scope"),
                "node_types": None if e.get("node_types") is None else types(e["node_types"], f"{where}.node_types")}
    if op in ("seq", "alt"):
        items = e.get("items")
        if isinstance(items, (str, bytes, Mapping)) or not isinstance(items, (list, tuple)) or not items:
            raise PathError(f"{where}.items is a non-empty list of expressions")
        flat: list[dict] = []
        for i, item in enumerate(items):
            x = _normal(item, scope, types, f"{where}.items[{i}]")
            flat.extend(x["items"] if x["op"] == op else [x])
        if op == "alt":
            flat = list({canonical_json(x): x for x in sorted(flat, key=canonical_json)}.values())
        return flat[0] if len(flat) == 1 else {"op": op, "items": flat}
    lo, hi = e.get("min", 0), e.get("max")
    if isinstance(lo, bool) or not isinstance(lo, int) or lo < 0:
        raise PathError(f"{where}.min is a non-negative integer, not {lo!r}")
    if hi is not None and (isinstance(hi, bool) or not isinstance(hi, int) or hi < max(lo, 1)):
        raise PathError(f"{where}.max is null or an integer of at least max(min, 1), not {hi!r}")
    if "item" not in e:
        raise PathError(f"{where}: a repeat needs an item")
    item = _normal(e["item"], scope, types, f"{where}.item")
    if lo == 1 and hi == 1:
        return item
    return {"op": "repeat", "item": item, "min": lo, "max": hi}


def steps_written_out(e: Mapping) -> int:
    """How many steps the expression has once its bounded repetitions are written out (its automaton's states,
    the start aside)."""
    op = e["op"]
    if op == "step":
        return 1
    if op in ("seq", "alt"):
        return sum(steps_written_out(x) for x in e["items"])
    n = steps_written_out(e["item"])
    return n * (max(e["min"], 1) if e["max"] is None else e["max"])


def to_text(e: Mapping) -> str:
    """The text syntax of a normalized expression (for messages and documentation); parsing it gives the same
    normal form when each step has one scope entry."""
    def name(x: str) -> str:
        return x if re.fullmatch(r"[A-Za-z0-9_.:\-]+", x) else f"`{x}`"

    def go(x: Mapping, outer: str) -> str:
        op = x["op"]
        if op == "step":
            parts = [("^" if s["direction"] == "reverse" else "") + name(s["relation"]) for s in x["scope"]]
            types = "" if x["node_types"] is None else "[" + ",".join(map(name, x["node_types"])) + "]"
            text = "|".join(p + types for p in parts)
            return f"({text})" if len(parts) > 1 and outer != "alt" else text
        if op == "seq":
            text = "/".join(go(i, "seq") for i in x["items"])
            return f"({text})" if outer == "repeat" else text
        if op == "alt":
            text = "|".join(go(i, "alt") for i in x["items"])
            return f"({text})" if outer in ("seq", "repeat") else text
        lo, hi = x["min"], x["max"]
        suffix = {(0, None): "*", (1, None): "+", (0, 1): "?"}.get((lo, hi))
        if suffix is None:
            suffix = "{%d}" % lo if lo == hi else "{%d,}" % lo if hi is None else "{%d,%d}" % (lo, hi)
        return go(x["item"], "repeat") + suffix
    return go(e, "top")


# ---------------------------------------------------------------------------------------------- the automaton
class Automaton(NamedTuple):
    """The Glushkov automaton of an expression: ``steps[i]`` (i >= 1) is the step that state i matched (``steps[0]``
    is None: the start), ``follow[q]`` the states one hop moves to from state q, in ascending order, and ``accepting``
    the states a matching walk ends in."""
    steps: tuple
    follow: tuple
    accepting: frozenset
    nullable: bool


def automaton(e: Mapping) -> Automaton:
    """Compile a normalized expression (Glushkov's construction: each written-out step is a state; first, last and
    follow sets give the moves). Bounded repetitions are written out as the module docstring says."""
    steps: list = [None]
    follow: list[set[int]] = [set()]

    def new(step: Mapping) -> int:
        steps.append(step)
        follow.append(set())
        return len(steps) - 1

    def link(last: set[int], first: set[int]) -> None:
        for x in last:
            follow[x] |= first

    def seq(parts: list) -> tuple[bool, set[int], set[int]]:
        """(nullable, first, last) of the parts one after another; each part compiles when called (in order)."""
        nullable, first, last = True, set(), set()
        for part in parts:
            n, f, l_ = part()
            link(last, f)
            if nullable:
                first |= f
            last = (last | l_) if n else set(l_)
            nullable = nullable and n
        return nullable, first, last

    def go(x: Mapping) -> tuple[bool, set[int], set[int]]:
        op = x["op"]
        if op == "step":
            i = new(x)
            return False, {i}, {i}
        if op == "seq":
            return seq([lambda item=item: go(item) for item in x["items"]])
        if op == "alt":
            nullable, first, last = False, set(), set()
            for item in x["items"]:
                n, f, l_ = go(item)
                nullable, first, last = nullable or n, first | f, last | l_
            return nullable, first, last
        item, lo, hi = x["item"], x["min"], x["max"]

        def star() -> tuple[bool, set[int], set[int]]:
            _, f, l_ = go(item)
            link(l_, f)
            return True, f, l_

        def plus() -> tuple[bool, set[int], set[int]]:
            n, f, l_ = go(item)
            link(l_, f)
            return n, f, l_

        def optional() -> tuple[bool, set[int], set[int]]:
            _, f, l_ = go(item)
            return True, f, l_

        if hi is None:
            if lo == 0:
                return star()
            return seq([lambda: go(item)] * (lo - 1) + [plus])
        return seq([lambda: go(item)] * lo + [optional] * (hi - lo))

    nullable, first, last = go(e)
    follow[0] = set(first)
    accepting = frozenset(last | ({0} if nullable else set()))
    return Automaton(tuple(steps), tuple(tuple(sorted(f)) for f in follow), accepting, nullable)
