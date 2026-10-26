"""Immutable, read-only graph views (projections), which every GRAPH operation binds.

A projection holds what a consumer's adapter supplies and nothing else: node records (an original ID and optional
types), edge records (an original ID, a source, a target and one relation), the declared relation and node-type
vocabularies, and the consumer's bindings (what the projection was built from: a source snapshot, a schema or
context, a policy scope). Records are remapped to dense integers for execution; the original IDs are kept and are
what every answer names. Parallel edges (two records with the same ends and relation) stay two records.

A projection never changes once built. Its identity is a sha256 digest of its canonical content and bindings
(:func:`_identity`), so two projections of the same records, in any order, share one identity, and a request names
the identity of the projection it was made for.

Candidate records (``kind: hypothesis_candidate``, or ``epistemic_status: unverified``) are refused: the v0
projection holds source records only, so a proposed relationship can never become a traversable edge
(docs/HYPOTHESES_AND_PROVENANCE.md).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any, NamedTuple

from ._canonical import canonical_json, encode_text, is_digest
from ._validation import shape_problems
from .errors import ProjectionError

CONTRACT = "graph-runtime/v0.1"
FORWARD, REVERSE = "forward", "reverse"
DIRECTIONS = (FORWARD, REVERSE)
PROJECTION_KIND = "graph_runtime.projection"


class NodeRecord(NamedTuple):
    id: str
    types: tuple[str, ...]


class EdgeRecord(NamedTuple):
    id: str
    source: str
    target: str
    relation: str


def _text(value: Any, what: str) -> str:
    """A non-empty string that encodes as UTF-8 (an ID, a relation, a type, a binding)."""
    if type(value) is str and value and value.isascii():      # the common case, decided without encoding
        return value
    if not isinstance(value, str) or not value:
        raise ProjectionError("invalid_record", f"{what} must be a non-empty string, not {value!r}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ProjectionError("invalid_record", f"{what} {value!r} is not valid Unicode text") from None
    return value


def _is_candidate(record: Any) -> bool:
    """Whether a record is a hypothesis candidate, which a v0 projection never holds: a ``HypothesisCandidate``, or a
    mapping with ``kind: hypothesis_candidate`` or ``epistemic_status: unverified``."""
    if type(record) is dict:
        return record.get("kind") == "hypothesis_candidate" or record.get("epistemic_status") == "unverified"
    if getattr(record, "kind", None) == "hypothesis_candidate":
        return True
    return isinstance(record, Mapping) and (record.get("kind") == "hypothesis_candidate"
                                            or record.get("epistemic_status") == "unverified")


def _names(values: Iterable[Any] | None, what: str) -> tuple[str, ...] | None:
    if values is None:
        return None
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ProjectionError("invalid_record", f"{what} must be a list of strings, not {values!r}")
    return tuple(sorted({_text(v, f"a {what[:-1]}") for v in values}))


class Projection:
    """An immutable graph view. Build one with :meth:`build` (or :meth:`from_dict`); it never changes afterwards."""

    __slots__ = ("_identity", "_bindings", "_relations", "_rel_index", "_node_types", "_type_index", "_node_ids",
                 "_node_index", "_node_mask", "_edge_ids", "_edge_index", "_edge_src", "_edge_dst", "_edge_rel",
                 "_out", "_in", "_sealed")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("build a Projection with Projection.build(...) or Projection.from_dict(...)")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a Projection is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("a Projection is immutable")

    def __reduce__(self):                      # a copy or a pickle rebuilds it from its records
        return (Projection.from_dict, (self.to_dict(),))

    # ------------------------------------------------------------------------------------------------ building
    @classmethod
    def build(cls, *, nodes: Iterable[Any], edges: Iterable[Mapping], relations: Iterable[str] | None = None,
              node_types: Iterable[str] | None = None, bindings: Mapping[str, str] | None = None) -> "Projection":
        """Build a projection from source records.

        ``nodes``: node IDs, or mappings ``{"id": str, "types": [str, ...]}``. ``edges``: mappings ``{"id", "source",
        "target", "relation"}`` (other keys are not read). ``relations`` and ``node_types``: the declared vocabularies,
        which may name relations or types no record uses (a request may then name them and get an empty, complete
        answer); when omitted, the ones the records use. ``bindings``: what the projection was built from, as
        ``{name: text}`` (suggested names: ``source``, ``schema``, ``policy``); they are part of its identity, and a
        caller passing the current bindings at execution learns when the projection has gone stale.

        Raises :class:`ProjectionError` for a duplicate ID, an edge whose source or target is not a node, a relation or
        type outside a declared vocabulary, or a hypothesis candidate. The records passed in are only read."""
        for name, records in (("nodes", nodes), ("edges", edges)):
            if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Iterable):
                raise ProjectionError("invalid_record", f"{name} must be an iterable of records")
        binds: dict[str, str] = {}
        if bindings is not None:
            if not isinstance(bindings, Mapping):
                raise ProjectionError("invalid_bindings", f"bindings must be a mapping of strings, not {bindings!r}")
            for k, v in bindings.items():
                binds[_text(k, "a binding name")] = _text(v, f"the binding {k!r}")

        declared_types = _names(node_types, "node_types")
        node_types_of: dict[str, tuple[str, ...]] = {}
        for rec in nodes:
            if _is_candidate(rec):
                raise ProjectionError("candidate_record", "a v0 projection holds source records only; hypothesis "
                                      "candidates stay in their own records and are never traversed")
            if isinstance(rec, str):
                nid, types = rec, ()
            elif isinstance(rec, Mapping):
                nid = rec.get("id")
                types = rec.get("types", ())
                if types is None:
                    types = ()
            else:
                raise ProjectionError("invalid_record", f"a node is an ID or a mapping with id and types, not {rec!r}")
            _text(nid, "a node ID")
            if isinstance(types, (str, bytes)) or not isinstance(types, Iterable):
                raise ProjectionError("invalid_record", f"the types of node {nid!r} must be a list of strings")
            if nid in node_types_of:
                raise ProjectionError("duplicate_node_id", f"the node ID {nid!r} is used twice")
            node_types_of[nid] = tuple(sorted({_text(t, f"a type of node {nid!r}") for t in types}))

        used_types = sorted({t for ts in node_types_of.values() for t in ts})
        if declared_types is None:
            declared_types = tuple(used_types)
        else:
            stray = [t for t in used_types if t not in set(declared_types)]
            if stray:
                raise ProjectionError("undeclared_node_type", f"node types {stray} are not in the declared node_types")

        declared_rel = _names(relations, "relations")
        edge_recs: dict[str, tuple[str, str, str]] = {}
        for rec in edges:
            if _is_candidate(rec):
                raise ProjectionError("candidate_record", "a v0 projection holds source records only; a proposed "
                                      "relation of a hypothesis candidate is never a traversable edge")
            if not isinstance(rec, Mapping):
                raise ProjectionError("invalid_record", f"an edge is a mapping with id, source, target and relation, "
                                      f"not {rec!r}")
            eid = _text(rec.get("id"), "an edge ID")
            src = _text(rec.get("source"), f"the source of edge {eid!r}")
            dst = _text(rec.get("target"), f"the target of edge {eid!r}")
            rel = _text(rec.get("relation"), f"the relation of edge {eid!r}")
            if eid in edge_recs:
                raise ProjectionError("duplicate_edge_id", f"the edge ID {eid!r} is used twice")
            for end in (src, dst):
                if end not in node_types_of:
                    raise ProjectionError("dangling_edge", f"edge {eid!r} names {end!r}, which is not a node")
            edge_recs[eid] = (src, dst, rel)

        used_rel = sorted({r for _, _, r in edge_recs.values()})
        if declared_rel is None:
            declared_rel = tuple(used_rel)
        else:
            stray = [r for r in used_rel if r not in set(declared_rel)]
            if stray:
                raise ProjectionError("undeclared_relation", f"relations {stray} are not in the declared relations")

        p = object.__new__(cls)
        node_ids = tuple(sorted(node_types_of))
        node_index = {nid: i for i, nid in enumerate(node_ids)}
        type_index = {t: i for i, t in enumerate(declared_types)}
        rel_index = {r: i for i, r in enumerate(declared_rel)}
        edge_ids = tuple(sorted(edge_recs))
        edge_src = tuple(node_index[edge_recs[e][0]] for e in edge_ids)
        edge_dst = tuple(node_index[edge_recs[e][1]] for e in edge_ids)
        edge_rel = tuple(rel_index[edge_recs[e][2]] for e in edge_ids)
        out: list[list[int]] = [[] for _ in node_ids]
        inc: list[list[int]] = [[] for _ in node_ids]
        for ei in range(len(edge_ids)):              # ascending edge ID: every adjacency list is in canonical order
            out[edge_src[ei]].append(ei)
            inc[edge_dst[ei]].append(ei)
        masks = []
        for nid in node_ids:
            m = 0
            for t in node_types_of[nid]:
                m |= 1 << type_index[t]
            masks.append(m)
        put = object.__setattr__
        put(p, "_bindings", MappingProxyType(dict(sorted(binds.items()))))
        put(p, "_relations", declared_rel)
        put(p, "_rel_index", MappingProxyType(rel_index))
        put(p, "_node_types", declared_types)
        put(p, "_type_index", MappingProxyType(type_index))
        put(p, "_node_ids", node_ids)
        put(p, "_node_index", MappingProxyType(node_index))
        put(p, "_node_mask", tuple(masks))
        put(p, "_edge_ids", edge_ids)
        put(p, "_edge_index", MappingProxyType({e: i for i, e in enumerate(edge_ids)}))
        put(p, "_edge_src", edge_src)
        put(p, "_edge_dst", edge_dst)
        put(p, "_edge_rel", edge_rel)
        put(p, "_out", tuple(tuple(x) for x in out))
        put(p, "_in", tuple(tuple(x) for x in inc))
        put(p, "_identity", _identity(p, node_types_of))
        put(p, "_sealed", True)
        return p

    @classmethod
    def from_dict(cls, data: Mapping) -> "Projection":
        """Rebuild a complete snapshot from :meth:`to_dict` and check its required identity."""
        if not isinstance(data, Mapping) or data.get("kind") != PROJECTION_KIND:
            raise ProjectionError("invalid_snapshot", f"a projection snapshot has kind {PROJECTION_KIND!r}")
        if data.get("contract") != CONTRACT:
            raise ProjectionError("unsupported_contract", f"this runtime reads contract {CONTRACT!r}, not "
                                  f"{data.get('contract')!r}")
        problems = shape_problems("projection", dict(data))
        if problems:
            raise ProjectionError("invalid_snapshot", "; ".join(problems))
        p = cls.build(nodes=data["nodes"], edges=data["edges"], relations=data["relations"],
                      node_types=data["node_types"], bindings=data["bindings"])
        if data["identity"] != p.identity:
            raise ProjectionError("identity_mismatch", f"the snapshot says {data['identity']}, its records give "
                                  f"{p.identity}")
        return p

    # ------------------------------------------------------------------------------------------------ reading
    @property
    def identity(self) -> str:
        """``sha256:<hex>``: the digest of the canonical content and bindings (:func:`_identity`)."""
        return self._identity

    @property
    def bindings(self) -> Mapping[str, str]:
        return self._bindings

    @property
    def relations(self) -> tuple[str, ...]:
        return self._relations

    @property
    def node_types(self) -> tuple[str, ...]:
        return self._node_types

    @property
    def node_count(self) -> int:
        return len(self._node_ids)

    @property
    def edge_count(self) -> int:
        return len(self._edge_ids)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._node_index

    def has_edge(self, edge_id: str) -> bool:
        return edge_id in self._edge_index

    def node(self, node_id: str) -> NodeRecord:
        i = self._node_index[node_id]
        m, types = self._node_mask[i], []
        for t, b in self._type_index.items():
            if m >> b & 1:
                types.append(t)
        return NodeRecord(node_id, tuple(sorted(types)))

    def edge(self, edge_id: str) -> EdgeRecord:
        i = self._edge_index[edge_id]
        return EdgeRecord(edge_id, self._node_ids[self._edge_src[i]], self._node_ids[self._edge_dst[i]],
                          self._relations[self._edge_rel[i]])

    def nodes(self) -> Iterable[NodeRecord]:
        """Every node, in ascending ID order."""
        return (self.node(n) for n in self._node_ids)

    def edges(self) -> Iterable[EdgeRecord]:
        """Every edge, in ascending ID order."""
        return (self.edge(e) for e in self._edge_ids)

    def stale_bindings(self, current: Mapping[str, str]) -> tuple[str, ...]:
        """The binding names whose value differs from ``current`` (or that one side lacks): empty while the projection
        still describes what it was built from."""
        names = set(self._bindings) | set(current)
        return tuple(sorted((n for n in names if n not in self._bindings or n not in current
                             or self._bindings[n] != current[n]), key=repr))

    def to_dict(self) -> dict:
        """A JSON-ready snapshot, with its identity; :meth:`from_dict` rebuilds and checks it."""
        return {"kind": PROJECTION_KIND, "contract": CONTRACT, "identity": self._identity,
                "bindings": dict(self._bindings), "relations": list(self._relations),
                "node_types": list(self._node_types),
                "nodes": [{"id": n.id, "types": list(n.types)} for n in self.nodes()],
                "edges": [e._asdict() for e in self.edges()]}

    def request(self, operation: str, **params: Any):
        """A normalized request for ``operation`` bound to this projection (see :func:`graph_runtime.normalize_request`
        for the parameters). Raises :class:`graph_runtime.errors.RequestError` when it is malformed or unsupported."""
        from .request import normalize_request
        return normalize_request({"operation": operation, "projection": self._identity, **params})

    def __repr__(self) -> str:
        return (f"<Projection {self._identity[:19]}… nodes={len(self._node_ids)} edges={len(self._edge_ids)} "
                f"relations={len(self._relations)}>")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Projection) and other._identity == self._identity

    def __hash__(self) -> int:
        return hash(self._identity)


def _identity(p: Projection, node_types_of: Mapping[str, tuple[str, ...]]) -> str:
    """The projection digest: sha256 over newline-terminated canonical JSON lines, a header, then one line per node in
    ascending ID order (``[id, [types]]``), then one per edge in ascending ID order (``[id, source, target,
    relation]``). The header holds the kind, the contract, the bindings, both vocabularies and the counts. Any
    implementation can reproduce it from the records alone; nothing depends on the integer remapping."""
    h = hashlib.sha256()
    header = {"kind": PROJECTION_KIND, "contract": CONTRACT, "bindings": dict(p._bindings),
              "relations": list(p._relations), "node_types": list(p._node_types),
              "node_count": len(p._node_ids), "edge_count": len(p._edge_ids)}
    h.update(canonical_json(header).encode("utf-8") + b"\n")
    # each line as canonical_json writes it (the same string encoder), hashed a chunk of lines at a time
    enc, ids, rels, lines = encode_text, p._node_ids, p._relations, []

    def flush() -> None:
        if lines:
            h.update(("\n".join(lines) + "\n").encode("utf-8"))
            lines.clear()
    for nid in ids:
        lines.append(f"[{enc(nid)},[{','.join(map(enc, node_types_of[nid]))}]]")
        if len(lines) == 8192:
            flush()
    for eid, s, t, r in zip(p._edge_ids, p._edge_src, p._edge_dst, p._edge_rel):
        lines.append(f"[{enc(eid)},{enc(ids[s])},{enc(ids[t])},{enc(rels[r])}]")
        if len(lines) == 8192:
            flush()
    flush()
    out = "sha256:" + h.hexdigest()
    assert is_digest(out)
    return out
