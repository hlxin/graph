"""Canonical JSON, digests and deep freezing: the one encoding every identity in GRAPH is computed from."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


def canonical_json(value: Any) -> str:
    """Sorted keys, no insignificant whitespace, text kept as it is (UTF-8), NaN and infinities refused."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    """``sha256:<hex>`` of the canonical JSON of ``value``."""
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def json_safe(value: Any) -> Any:
    """``value`` when canonical JSON can hold it (text that encodes, a finite number, a bool, null, and lists and
    mappings of those), else its ``repr``: what an error may quote of a request it refuses."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
            return value
        except UnicodeEncodeError:
            return repr(value)
    if isinstance(value, (int, float)):
        try:
            canonical_json(value)
            return value
        except ValueError:
            return repr(value) if not isinstance(value, int) else f"<an integer of {value.bit_length()} bits>"
    if isinstance(value, Mapping):
        return {json_safe(k) if isinstance(k, str) else repr(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return repr(value)


def is_digest(text: Any) -> bool:
    """Whether ``text`` is a digest as :func:`digest` writes it."""
    return (isinstance(text, str) and len(text) == 71 and text.startswith("sha256:")
            and all(c in "0123456789abcdef" for c in text[7:]))


def freeze(value: Any) -> Any:
    """A read-only copy: mappings become mapping proxies over fresh dicts, lists and tuples become tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    """A plain, JSON-ready copy of a frozen value: dicts and lists that share nothing with it."""
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in value]
    return value
