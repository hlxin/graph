"""Immutable, read-only graph views (projections), which every GRAPH operation binds.

A projection holds what a consumer's adapter supplies and nothing else: node records (an original ID and optional
types), edge records (an original ID, a source, a target and one relation), the declared relation and node-type
vocabularies, and the consumer's bindings (what the projection was built from: a source snapshot, a schema or
context, a policy scope). Records are remapped to dense integers for execution; the original IDs are kept and are
what every answer names. Parallel edges (two records with the same ends and relation) stay two records.

A projection never changes once built. Its identity is a sha256 digest of its canonical content and bindings
(:func:`_identity`), so two projections of the same records, in any order, share one identity, and a request names
the identity of the projection it was made for. The engine keeps indexes it derives from that content (each scope's
hop lists, say) in ``_memo``: they are computed on demand, never change once made, and are neither part of the
identity nor visible in any answer.

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

CONTRACT = "graph-runtime/v0.2"
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


def _hierarchy(value: Any, what: str) -> dict[str, tuple[str, ...]]:
    """``{name: (parents, sorted)}`` of a hierarchy, checked to be acyclic; names without parents are left out."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ProjectionError("invalid_hierarchy", f"{what}_parents is a mapping of a {what} to its parents")
    out: dict[str, tuple[str, ...]] = {}
    for child, parents in value.items():
        name = _text(child, f"a {what} in {what}_parents")
        if isinstance(parents, (str, bytes)) or not isinstance(parents, Iterable):
            raise ProjectionError("invalid_hierarchy", f"the parents of {what} {name!r} are a list of names")
        ps = tuple(sorted({_text(x, f"a parent of {what} {name!r}") for x in parents}))
        if ps:
            out[name] = ps
    state: dict[str, int] = {}                      # 1: on the current walk, 2: done
    for start in out:
        stack = [(start, iter(out.get(start, ())))]
        state[start] = state.get(start) or 1
        if state[start] == 2:
            continue
        while stack:
            node, parents = stack[-1]
            nxt = next(parents, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                raise ProjectionError("hierarchy_cycle", f"the {what} hierarchy has a cycle through {nxt!r}")
            elif state.get(nxt) is None:
                state[nxt] = 1
                stack.append((nxt, iter(out.get(nxt, ()))))
    return dict(sorted(out.items()))


def _named(hierarchy: Mapping[str, tuple[str, ...]]) -> set[str]:
    return set(hierarchy) | {p for ps in hierarchy.values() for p in ps}


def _children(hierarchy: Mapping[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for child, parents in hierarchy.items():
        for parent in parents:
            out.setdefault(parent, []).append(child)
    return {k: tuple(sorted(v)) for k, v in out.items()}


def _closure(up: Mapping[str, tuple[str, ...]]) -> dict[str, frozenset[str]]:
    """Every name's transitive ``up`` set (its ancestors, for a parents map), for an acyclic map."""
    memo: dict[str, frozenset[str]] = {}

    def walk(name: str) -> frozenset[str]:
        if name not in memo:
            acc: set[str] = set()
            for nxt in up.get(name, ()):
                acc.add(nxt)
                acc |= walk(nxt)
            memo[name] = frozenset(acc)
        return memo[name]
    for name in up:
        walk(name)
    return memo


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
                 "_type_parents", "_relation_parents", "_rel_cover", "_declared_mask",
                 "_out", "_in", "_memo", "_sealed")

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
              node_types: Iterable[str] | None = None, bindings: Mapping[str, str] | None = None,
              type_parents: Mapping[str, Iterable[str]] | None = None,
              relation_parents: Mapping[str, Iterable[str]] | None = None) -> "Projection":
        """Build a projection from source records.

        ``nodes``: node IDs, or mappings ``{"id": str, "types": [str, ...]}``. ``edges``: mappings ``{"id", "source",
        "target", "relation"}`` (other keys are not read). ``relations`` and ``node_types``: the declared vocabularies,
        which may name relations or types no record uses (a request may then name them and get an empty, complete
        answer); when omitted, the ones the records use. ``bindings``: what the projection was built from, as
        ``{name: text}`` (suggested names: ``source``, ``schema``, ``policy``); they are part of its identity, and a
        caller passing the current bindings at execution learns when the projection has gone stale.

        ``type_parents`` and ``relation_parents``: hierarchies, as ``{name: [parent, ...]}`` (acyclic; several
        parents allowed). A node has every ancestor of the types it declares, so a constraint on a type matches the
        nodes of its subtypes; a scope on a relation also follows the relations below it (a step on CONTROL_FOR follows
        DARK_CONTROL_FOR). Witnesses still name each edge's own relation.

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

        tparents = _hierarchy(type_parents, "type")
        rparents = _hierarchy(relation_parents, "relation")
        declared_types = _names(node_types, "node_types")
        node_types_of: dict[str, tuple[str, ...]] = {}
        for rec in nodes:
            if type(rec) is dict and rec.get("kind") != "hypothesis_candidate" \
                    and rec.get("epistemic_status") != "unverified":
                # the common record, checked inline with no message built unless a check fails
                nid, types = rec.get("id"), rec.get("types", ())
                if type(nid) is str and nid and nid.isascii() and nid not in node_types_of and (
                        types is None or type(types) in (list, tuple)
                        and all(type(t) is str and t and t.isascii() for t in types)):
                    node_types_of[nid] = tuple(sorted(set(types))) if types else ()
                    continue
            elif type(rec) is str and rec and rec.isascii() and rec not in node_types_of:
                node_types_of[rec] = ()
                continue
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

        used_types = sorted({t for ts in node_types_of.values() for t in ts} | _named(tparents))
        if declared_types is None:
            declared_types = tuple(used_types)
        else:
            stray = [t for t in used_types if t not in set(declared_types)]
            if stray:
                raise ProjectionError("undeclared_node_type", f"node types {stray} are not in the declared node_types")

        declared_rel = _names(relations, "relations")
        edge_recs: dict[str, tuple[str, str, str]] = {}
        for rec in edges:
            if type(rec) is dict and rec.get("kind") != "hypothesis_candidate" \
                    and rec.get("epistemic_status") != "unverified":
                # the common record, checked inline with no message built unless a check fails
                eid, src, dst, rel = rec.get("id"), rec.get("source"), rec.get("target"), rec.get("relation")
                if type(eid) is str and eid and eid.isascii() and eid not in edge_recs \
                        and src in node_types_of and dst in node_types_of \
                        and type(rel) is str and rel and rel.isascii():
                    edge_recs[eid] = (src, dst, rel)
                    continue
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

        used_rel = sorted({r for _, _, r in edge_recs.values()} | _named(rparents))
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
        srcs, dsts, rels = zip(*map(edge_recs.__getitem__, edge_ids)) if edge_ids else ((), (), ())
        edge_src = tuple(map(node_index.__getitem__, srcs))
        edge_dst = tuple(map(node_index.__getitem__, dsts))
        edge_rel = tuple(map(rel_index.__getitem__, rels))
        out: list[list[int]] = [[] for _ in node_ids]
        inc: list[list[int]] = [[] for _ in node_ids]
        for ei in range(len(edge_ids)):              # ascending edge ID: every adjacency list is in canonical order
            out[edge_src[ei]].append(ei)
            inc[edge_dst[ei]].append(ei)
        ancestors = _closure(tparents)                 # type -> the types above it
        type_bits = {t: (1 << type_index[t]) | sum(1 << type_index[a] for a in ancestors.get(t, ()))
                     for t in declared_types}
        masks, declared = [], []                       # with the types' ancestors, and as declared
        for nid in node_ids:
            m = d = 0
            for t in node_types_of[nid]:
                m |= type_bits[t]
                d |= 1 << type_index[t]
            masks.append(m)
            declared.append(d)
        below = _closure(_children(rparents))           # relation -> the relations below it
        cover = tuple(tuple(sorted({rel_index[r]} | {rel_index[d] for d in below.get(r, ())}))
                      for r in declared_rel)
        put = object.__setattr__
        put(p, "_bindings", MappingProxyType(dict(sorted(binds.items()))))
        put(p, "_relations", declared_rel)
        put(p, "_rel_index", MappingProxyType(rel_index))
        put(p, "_node_types", declared_types)
        put(p, "_type_index", MappingProxyType(type_index))
        put(p, "_node_ids", node_ids)
        put(p, "_node_index", MappingProxyType(node_index))
        put(p, "_node_mask", tuple(masks))
        put(p, "_declared_mask", tuple(declared))
        put(p, "_edge_ids", edge_ids)
        put(p, "_edge_index", MappingProxyType({e: i for i, e in enumerate(edge_ids)}))
        put(p, "_edge_src", edge_src)
        put(p, "_edge_dst", edge_dst)
        put(p, "_edge_rel", edge_rel)
        put(p, "_out", tuple(map(tuple, out)))
        put(p, "_in", tuple(map(tuple, inc)))
        put(p, "_type_parents", MappingProxyType(tparents))
        put(p, "_relation_parents", MappingProxyType(rparents))
        put(p, "_rel_cover", cover)
        put(p, "_identity", _identity(p, node_types_of))
        put(p, "_memo", {})
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
                      node_types=data["node_types"], bindings=data["bindings"], type_parents=data["type_parents"],
                      relation_parents=data["relation_parents"])
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
    def type_parents(self) -> Mapping[str, tuple[str, ...]]:
        """The type hierarchy: each type's parents (types without parents are left out)."""
        return self._type_parents

    @property
    def relation_parents(self) -> Mapping[str, tuple[str, ...]]:
        """The relation hierarchy: each relation's parents (relations without parents are left out)."""
        return self._relation_parents

    def covered(self, relation: str) -> tuple[str, ...]:
        """The relations a scope on ``relation`` follows: itself and every relation below it."""
        return tuple(self._relations[i] for i in self._rel_cover[self._rel_index[relation]])

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
        m, types = self._declared_mask[i], []
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
                "type_parents": {k: list(v) for k, v in self._type_parents.items()},
                "relation_parents": {k: list(v) for k, v in self._relation_parents.items()},
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
              "type_parents": {k: list(v) for k, v in p._type_parents.items()},
              "relation_parents": {k: list(v) for k, v in p._relation_parents.items()},
              "node_count": len(p._node_ids), "edge_count": len(p._edge_ids)}
    h.update(canonical_json(header).encode("utf-8") + b"\n")
    # each line as canonical_json writes it (the same string encoder), hashed a chunk of lines at a time; a node ID,
    # relation or type is encoded once, however many lines name it
    enc, ids, chunk = encode_text, p._node_ids, 8192
    node_text = list(map(enc, ids))
    rel_text = list(map(enc, p._relations))
    type_text = {t: enc(t) for t in p._node_types}
    for at in range(0, len(ids), chunk):
        h.update("".join([f"[{node_text[i]},[{','.join([type_text[t] for t in node_types_of[ids[i]]])}]]\n"
                          for i in range(at, min(at + chunk, len(ids)))]).encode("utf-8"))
    for at in range(0, len(p._edge_ids), chunk):
        part = slice(at, at + chunk)
        h.update("".join([f"[{enc(e)},{node_text[s]},{node_text[t]},{rel_text[r]}]\n" for e, s, t, r in
                          zip(p._edge_ids[part], p._edge_src[part], p._edge_dst[part], p._edge_rel[part])])
                 .encode("utf-8"))
    out = "sha256:" + h.hexdigest()
    assert is_digest(out)
    return out
