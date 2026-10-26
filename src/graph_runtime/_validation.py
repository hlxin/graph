"""Standard-library checks for the subset of JSON Schema used by our bundled contract.

This is deliberately not a general JSON Schema implementation. Keeping record shape checks on the bundled
definitions lets transport readers and the published schema enforce the same rules. Cross-record semantics live
with the record readers. Tests compare these checks with an independent Draft 2020-12 validator.

Each definition is compiled once into closures (:func:`_compile`). A compiled node runs in one of two modes: with a
list it appends every problem, in the order the keywords are checked, and with ``None`` it is a predicate that stops
at the first problem and writes no message (what ``if``, ``anyOf``, ``oneOf`` and ``contains`` need). A result
of tens of thousands of entries is then checked in a fraction of a second rather than many seconds.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from .schemas import json_schema

Node = Callable[[Any, str, "list[str] | None"], bool]


def _atom_problem(value):
    """A scalar's problem (None when it is plain JSON), or ``...`` for a container to walk."""
    kind = type(value)
    if value is None or kind is bool or kind is int:
        return None
    if kind is str or isinstance(value, str):
        if not value.isascii():
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                return "text must be valid Unicode"
        return None
    if kind is float or isinstance(value, float):
        return None if math.isfinite(value) else "numbers must be finite"
    if isinstance(value, (dict, list)):
        return ...
    return "values must be plain JSON"


def json_problem(value, active=None):
    """Reject values JSON would silently coerce, non-finite numbers and invalid Unicode, including object keys."""
    problem = _atom_problem(value)
    if problem is not ...:
        return problem
    active = set() if active is None else active
    if id(value) in active:
        return "JSON cannot contain a cycle"
    active.add(id(value))
    try:
        if isinstance(value, dict):
            for key in value:
                if type(key) is str and key.isascii():
                    continue
                if not isinstance(key, str):
                    return "JSON object keys must be strings"
                problem = _atom_problem(key)
                if problem:
                    return problem
        for item in value.values() if isinstance(value, dict) else value:
            kind = type(item)
            if item is None or kind is bool or kind is int or (kind is str and item.isascii()):
                continue                       # the common scalars, decided without a call
            problem = _atom_problem(item)
            if problem is ...:
                problem = json_problem(item, active)
            if problem:
                return problem
    finally:
        active.remove(id(value))
    return None


@lru_cache(maxsize=1)
def _definitions():
    return json_schema()["$defs"]


def shape_problems(entry, value):
    """Shape problems in a plain JSON record, without digest or graph checks."""
    problem = json_problem(value)
    if problem:
        return [problem]
    out: list[str] = []
    _compiled(entry)(value, entry, out)
    return out


_COMPILING: set[str] = set()


@lru_cache(maxsize=None)
def _compiled(name: str) -> Node:
    _COMPILING.add(name)
    try:
        return _compile(_definitions()[name])
    finally:
        _COMPILING.discard(name)


def _equal(a: Any, b: Any) -> bool:
    """JSON equality distinguishes booleans from numbers, including inside containers."""
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    return a == b


_TYPES: dict[str, Callable[[Any], bool]] = {
    "null": lambda v: v is None,
    "boolean": lambda v: type(v) is bool,
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: type(v) is int or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: type(v) in (int, float),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def _duplicates(items: list) -> bool:
    if all(type(x) is str for x in items):
        return len(set(items)) != len(items)
    return any(items[i] in items[:i] for i in range(len(items)))


def _compile(schema: Any) -> Node:
    """A closure that checks ``schema``, as the keywords are listed in :func:`_check_order`."""
    if schema is True:
        return lambda v, p, out: True
    if schema is False:
        def forbidden(v, p, out):
            if out is not None:
                out.append(f"{p}: field is not permitted")
            return False
        return forbidden
    if "$ref" in schema:                  # a reference stands alone
        name = schema["$ref"].removeprefix("#/$defs/")
        if name not in _COMPILING:        # resolved now; a definition that refers to itself is resolved on first use
            return _compiled(name)
        return lambda v, p, out: _compiled(name)(v, p, out)

    steps: list[Node] = []
    types = schema.get("type")
    type_ok = None
    if types is not None:
        tests = [_TYPES[t] for t in ([types] if isinstance(types, str) else types)]
        type_ok = tests[0] if len(tests) == 1 else (lambda v, tests=tests: any(t(v) for t in tests))

    if "const" in schema:
        const = schema["const"]

        def const_step(v, p, out):
            if _equal(v, const):
                return True
            if out is not None:
                out.append(f"{p}: expected {const!r}")
            return False
        steps.append(const_step)
    if "enum" in schema:
        options = schema["enum"]

        def enum_step(v, p, out):
            if any(_equal(v, o) for o in options):
                return True
            if out is not None:
                out.append(f"{p}: expected one of {options!r}")
            return False
        steps.append(enum_step)
    if "allOf" in schema:
        parts = [_compile(s) for s in schema["allOf"]]

        def all_step(v, p, out):
            ok = True
            for part in parts:
                if not part(v, p, out):
                    ok = False
                    if out is None:
                        return False
            return ok
        steps.append(all_step)
    if "anyOf" in schema:
        parts_any = [_compile(s) for s in schema["anyOf"]]

        def any_step(v, p, out):
            if any(part(v, p, None) for part in parts_any):
                return True
            if out is not None:
                out.append(f"{p}: does not match anyOf")
            return False
        steps.append(any_step)
    one_of = schema.get("oneOf")
    if one_of is not None and len(one_of) == 2 and one_of[1] == {"type": "null"}:
        # a nullable schema: a value other than null can match only the first branch, and whether null matches
        # exactly one branch is fixed once the first is compiled
        first = _compile(one_of[0])
        null_once: list[bool] = []

        def nullable_step(v, p, out):
            if v is not None:
                ok = first(v, p, None)
            else:
                if not null_once:
                    null_once.append(not first(None, p, None))
                ok = null_once[0]
            if not ok and out is not None:
                out.append(f"{p}: does not match oneOf")
            return ok
        steps.append(nullable_step)
    elif one_of is not None:
        parts_one = [_compile(s) for s in one_of]

        def one_step(v, p, out):
            count = 0
            for part in parts_one:
                if part(v, p, None):
                    count += 1
                    if count > 1:
                        break
            if count == 1:
                return True
            if out is not None:
                out.append(f"{p}: does not match oneOf")
            return False
        steps.append(one_step)
    if "if" in schema:
        cond = _compile(schema["if"])
        then = _compile(schema.get("then", {}))
        other = _compile(schema.get("else", {}))
        steps.append(lambda v, p, out: (then if cond(v, p, None) else other)(v, p, out))

    required = tuple(schema.get("required", ()))
    props = {k: _compile(s) for k, s in schema.get("properties", {}).items()}
    extra_schema = schema.get("additionalProperties", {})
    extra = None if extra_schema is True or extra_schema == {} else (False if extra_schema is False
                                                                      else _compile(extra_schema))
    names = _compile(schema["propertyNames"]) if "propertyNames" in schema else None
    min_props, max_props = schema.get("minProperties"), schema.get("maxProperties")
    if required or props or extra is not None or names is not None or min_props is not None or max_props is not None:
        def object_step(v, p, out):
            if not isinstance(v, dict):
                return True
            ok = True
            for k in required:
                if k not in v:
                    if out is None:
                        return False
                    out.append(f"{p}: missing {k}")
                    ok = False
            for k, item in v.items():
                if names is not None and not names(k, f"{p} key", out):
                    ok = False
                    if out is None:
                        return False
                sub = props.get(k, extra)
                if sub is None:
                    continue
                if sub is False:
                    if out is None:
                        return False
                    out.append(f"{p}: unexpected {k}")
                    ok = False
                elif not sub(item, f"{p}.{k}" if out is not None else p, out):
                    ok = False
                    if out is None:
                        return False
            if min_props is not None and len(v) < min_props:
                if out is None:
                    return False
                out.append(f"{p}: violates minProperties")
                ok = False
            if max_props is not None and len(v) > max_props:
                if out is None:
                    return False
                out.append(f"{p}: violates maxProperties")
                ok = False
            return ok
        steps.append(object_step)

    min_items, max_items = schema.get("minItems"), schema.get("maxItems")
    unique = schema.get("uniqueItems", False)
    prefix = [_compile(s) for s in schema.get("prefixItems", [])]
    items = _compile(schema["items"]) if "items" in schema else None
    contains = _compile(schema["contains"]) if "contains" in schema else None
    if min_items is not None or max_items is not None or unique or prefix or items is not None or contains is not None:
        def array_step(v, p, out):
            if not isinstance(v, list):
                return True
            ok = True
            for bad, message in ((min_items is not None and len(v) < min_items, "violates minItems"),
                                 (max_items is not None and len(v) > max_items, "violates maxItems"),
                                 (unique and _duplicates(v), "duplicate items")):
                if bad:
                    if out is None:
                        return False
                    out.append(f"{p}: {message}")
                    ok = False
            if prefix or items is not None:
                for i, item in enumerate(v):
                    check = prefix[i] if i < len(prefix) else items
                    if check is not None and not check(item, f"{p}[{i}]" if out is not None else p, out):
                        ok = False
                        if out is None:
                            return False
            if contains is not None and not any(contains(item, p, None) for item in v):
                if out is None:
                    return False
                out.append(f"{p}: missing required item")
                ok = False
            return ok
        steps.append(array_step)

    min_length = schema.get("minLength")
    pattern = re.compile(schema["pattern"]) if "pattern" in schema else None
    if min_length is not None or pattern is not None:
        def string_step(v, p, out):
            if not isinstance(v, str):
                return True
            ok = True
            if min_length is not None and len(v) < min_length:
                if out is None:
                    return False
                out.append(f"{p}: text is too short")
                ok = False
            if pattern is not None and pattern.search(v) is None:
                if out is None:
                    return False
                out.append(f"{p}: text does not match {schema['pattern']}")
                ok = False
            return ok
        steps.append(string_step)

    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if minimum is not None or maximum is not None:
        def number_step(v, p, out):
            if type(v) not in (int, float):
                return True
            ok = True
            if minimum is not None and v < minimum:
                if out is None:
                    return False
                out.append(f"{p}: below minimum")
                ok = False
            if maximum is not None and v > maximum:
                if out is None:
                    return False
                out.append(f"{p}: above maximum")
                ok = False
            return ok
        steps.append(number_step)

    def node(v, p, out):
        if type_ok is not None and not type_ok(v):
            if out is not None:
                out.append(f"{p}: expected {types}")
            return False
        ok = True
        for step in steps:
            if not step(v, p, out):
                ok = False
                if out is None:
                    return False
        return ok
    return node
