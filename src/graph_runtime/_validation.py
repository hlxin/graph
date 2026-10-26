"""Standard-library checks for the subset of JSON Schema used by our bundled contract.

This is deliberately not a general JSON Schema implementation. Keeping record shape checks on the bundled
definitions lets transport readers and the published schema enforce the same rules. Cross-record semantics live
with the record readers. Tests compare these checks with an independent Draft 2020-12 validator.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

from .schemas import json_schema


def json_problem(value, active=None):
    """Reject values JSON would silently coerce, non-finite numbers and invalid Unicode, including object keys."""
    active = set() if active is None else active
    if value is None or type(value) in (bool, int):
        return None
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return "text must be valid Unicode"
        return None
    if isinstance(value, float):
        return None if math.isfinite(value) else "numbers must be finite"
    if not isinstance(value, (dict, list)):
        return "values must be plain JSON"
    if id(value) in active:
        return "JSON cannot contain a cycle"
    active.add(id(value))
    try:
        if isinstance(value, dict):
            for key in value:
                if not isinstance(key, str):
                    return "JSON object keys must be strings"
                problem = json_problem(key, active)
                if problem:
                    return problem
        for item in value.values() if isinstance(value, dict) else value:
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
    return _check(_definitions()[entry], value, entry)


def _check(schema, value, path):
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: field is not permitted"]
    if "$ref" in schema:
        return _check(_definitions()[schema["$ref"].removeprefix("#/$defs/")], value, path)
    out = []
    matches = {
        "null": value is None, "boolean": type(value) is bool, "string": isinstance(value, str),
        "integer": type(value) is int or (isinstance(value, float) and value.is_integer()),
        "number": type(value) in (int, float), "object": isinstance(value, dict), "array": isinstance(value, list),
    }
    types = schema.get("type")
    if types is not None and not any(matches[t] for t in ([types] if isinstance(types, str) else types)):
        return [f"{path}: expected {types}"]
    # JSON equality distinguishes booleans from numbers, including inside containers.
    def equal(a, b):
        if type(a) is bool or type(b) is bool:
            return type(a) is type(b) and a == b
        return a == b
    if "const" in schema and not equal(value, schema["const"]):
        out.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and not any(equal(value, v) for v in schema["enum"]):
        out.append(f"{path}: expected one of {schema['enum']!r}")
    for key in ("allOf", "anyOf", "oneOf"):
        if key in schema:
            branches = [_check(s, value, path) for s in schema[key]]
            count = sum(not b for b in branches)
            if key == "allOf":
                out.extend(p for b in branches for p in b)
            elif (key == "anyOf" and not count) or (key == "oneOf" and count != 1):
                out.append(f"{path}: does not match {key}")
    if "if" in schema:
        branch = "else" if _check(schema["if"], value, path) else "then"
        out.extend(_check(schema.get(branch, {}), value, path))
    if isinstance(value, dict):
        out.extend(f"{path}: missing {k}" for k in schema.get("required", ()) if k not in value)
        props = schema.get("properties", {})
        for k, v in value.items():
            if "propertyNames" in schema:
                out.extend(_check(schema["propertyNames"], k, f"{path} key"))
            sub = props.get(k, schema.get("additionalProperties", {}))
            if sub is False:
                out.append(f"{path}: unexpected {k}")
            elif sub is not True:
                out.extend(_check(sub, v, f"{path}.{k}"))
        for key, bad in (("minProperties", len(value) < schema.get("minProperties", 0)),
                         ("maxProperties", len(value) > schema.get("maxProperties", len(value)))):
            if bad:
                out.append(f"{path}: violates {key}")
    if isinstance(value, list):
        for key, bad in (("minItems", len(value) < schema.get("minItems", 0)),
                         ("maxItems", len(value) > schema.get("maxItems", len(value)))):
            if bad:
                out.append(f"{path}: violates {key}")
        if schema.get("uniqueItems") and any(value[i] in value[:i] for i in range(len(value))):
            out.append(f"{path}: duplicate items")
        prefix = schema.get("prefixItems", [])
        for i, v in enumerate(value):
            out.extend(_check(prefix[i] if i < len(prefix) else schema.get("items", {}), v, f"{path}[{i}]"))
        if "contains" in schema and not any(not _check(schema["contains"], v, path) for v in value):
            out.append(f"{path}: missing required item")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{path}: text is too short")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            out.append(f"{path}: text does not match {schema['pattern']}")
    if type(value) in (int, float):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: above maximum")
    return out
