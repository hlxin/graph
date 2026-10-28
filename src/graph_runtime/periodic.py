"""Periodic projections: one cell of an infinite periodic graph, searched without lifting it.

A crystal, a slab or a polymer is an infinite graph that repeats: the derived graph of a finite *quotient graph*
whose edges carry integer *image shifts* (a voltage graph, in the terms of Gross and Tucker; the quotient graph of a
crystal net, in those of Chung, Hahn and Klee, and of Delgado-Friedrichs and O'Keeffe). A periodic projection holds
the cell's records: its nodes, and its edges each with a shift ``[s1, ..., sd]``. It stands for the infinite graph
whose nodes are the images of the cell's nodes, ``<node>@<k1>,...,<kd>`` for every integer vector k, and whose edges
are the images of the cell's edges: edge e from u to v with shift s joins ``u@k`` to ``v@(k + s)`` for every k, and
that image of e is named ``<e>@<k>`` (by the image of its source).

A search on a periodic projection reads only the images it reaches: no box of images is built first, so a context
of a few hundred atom images costs what those images cost, not what a box around them would. Its answers are those
of the infinite graph, in the canonical order every operation uses, by these image IDs: a finite lift that holds
every image a search reaches gives the same nodes, depths, witnesses and counters. ``periodic`` (one flag per axis,
all true by default) names the axes the graph repeats along; along any other, shifts and images are 0.

The infinite graph has no end, so a search with no depth bound that keeps finding images stops at its limits and is
interrupted, never complete: an interrupted answer certifies nothing absent. Reachability and dependency impact run
on periodic projections; the other operations are refused as unsupported.
"""

from __future__ import annotations

import hashlib
import re
from collections import deque
from collections.abc import Iterable, Mapping
from types import MappingProxyType, SimpleNamespace
from typing import Any

from ._canonical import canonical_json, encode_text, is_digest
from ._validation import shape_problems
from .errors import ProjectionError, RequestError
from .projection import CONTRACT, Projection

PERIODIC_KIND = "graph_runtime.periodic_projection"
PERIODIC_OPERATIONS = ("reachability", "dependency_impact", "periodicity")
_INTEGER = re.compile(r"(0|-?[1-9][0-9]*)")


def image_id(node: str, image: Iterable[int]) -> str:
    """The ID of an image of a cell's node (or edge): ``<id>@<k1>,...,<kd>``."""
    return f"{node}@{','.join(map(str, image))}"


def parse_image_id(text: str, dimensions: int) -> tuple[str, tuple[int, ...]] | None:
    """(the cell's ID, the image) of an image ID written canonically (integers in plain decimal), or None."""
    if not isinstance(text, str):
        return None
    base, at, image = text.rpartition("@")
    if not at or not base:
        return None
    parts = image.split(",")
    if len(parts) != dimensions or not all(_INTEGER.fullmatch(x) for x in parts):
        return None                                           # one way to write each image: no +, no leading zeros
    return base, tuple(int(x) for x in parts)


def _shift(value: Any, edge: str, dimensions: int | None) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, (list, tuple)):
        raise ProjectionError("invalid_record", f"the shift of edge {edge!r} is a list of integers")
    if any(isinstance(x, bool) or not isinstance(x, int) for x in value):
        raise ProjectionError("invalid_record", f"the shift of edge {edge!r} holds a value that is not an integer")
    if dimensions is not None and len(value) != dimensions:
        raise ProjectionError("invalid_record", f"the shift of edge {edge!r} has {len(value)} components; the "
                              f"projection has {dimensions} dimensions")
    return tuple(int(x) for x in value)


class PeriodicProjection:
    """An immutable periodic graph view: a cell's records with image shifts. Build one with :meth:`build` (or
    :meth:`from_dict`)."""

    __slots__ = ("_cell", "_dimensions", "_periodic", "_shifts", "_identity", "_memo", "_sealed")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("build a PeriodicProjection with PeriodicProjection.build(...) or .from_dict(...)")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a PeriodicProjection is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("a PeriodicProjection is immutable")

    def __reduce__(self):
        return (PeriodicProjection.from_dict, (self.to_dict(),))

    @classmethod
    def build(cls, *, nodes: Iterable[Any], edges: Iterable[Mapping], dimensions: int | None = None,
              periodic: Iterable[bool] | None = None, relations: Iterable[str] | None = None,
              node_types: Iterable[str] | None = None, bindings: Mapping[str, str] | None = None,
              type_parents: Mapping[str, Iterable[str]] | None = None,
              relation_parents: Mapping[str, Iterable[str]] | None = None) -> "PeriodicProjection":
        """Build a periodic projection from a cell's records: ``nodes`` and ``edges`` as
        :meth:`graph_runtime.Projection.build` takes them, each edge with a ``shift`` (a list of ``dimensions``
        integers: the image its target is in, relative to its source's). ``dimensions`` defaults to the length of
        the shifts; ``periodic`` to every axis. Raises :class:`ProjectionError` as a projection does, and for a
        malformed shift, a shift along an axis that does not repeat, or no dimensions."""
        edges = list(edges) if not isinstance(edges, (str, bytes, Mapping)) else edges
        if isinstance(edges, (str, bytes, Mapping)) or not isinstance(edges, list):
            raise ProjectionError("invalid_record", "edges must be an iterable of records")
        if dimensions is not None and (isinstance(dimensions, bool) or not isinstance(dimensions, int)
                                       or dimensions < 1):
            raise ProjectionError("invalid_record", "dimensions is a positive integer")
        shifts = {}
        for e in edges:
            if not isinstance(e, Mapping):
                raise ProjectionError("invalid_record", f"an edge is a mapping with id, source, target, relation and "
                                      f"shift, not {e!r}")
            s = _shift(e.get("shift"), e.get("id"), dimensions)
            if dimensions is None:
                dimensions = len(s)
                if dimensions < 1:
                    raise ProjectionError("invalid_record", "a shift has at least one component")
            shifts[e.get("id")] = s
        if dimensions is None:
            raise ProjectionError("invalid_record", "a periodic projection without edges states its dimensions")
        if periodic is None:
            axes = (True,) * dimensions
        else:
            axes = tuple(periodic) if not isinstance(periodic, (str, bytes, Mapping)) else ()
            if len(axes) != dimensions or any(not isinstance(x, bool) for x in axes):
                raise ProjectionError("invalid_record", f"periodic is a list of {dimensions} booleans")
        for eid, s in shifts.items():
            if any(x and not axes[i] for i, x in enumerate(s)):
                raise ProjectionError("invalid_record", f"edge {eid!r} shifts along an axis that does not repeat")
        cell = Projection.build(nodes=nodes, edges=edges, relations=relations, node_types=node_types,
                                bindings=bindings, type_parents=type_parents, relation_parents=relation_parents)
        p = object.__new__(cls)
        put = object.__setattr__
        put(p, "_cell", cell)
        put(p, "_dimensions", dimensions)
        put(p, "_periodic", axes)
        put(p, "_shifts", tuple(shifts[e] for e in cell._edge_ids))
        put(p, "_identity", _identity(p))
        put(p, "_memo", {})
        put(p, "_sealed", True)
        return p

    @classmethod
    def from_dict(cls, data: Mapping) -> "PeriodicProjection":
        """Rebuild a snapshot from :meth:`to_dict` and check its identity."""
        if not isinstance(data, Mapping) or data.get("kind") != PERIODIC_KIND:
            raise ProjectionError("invalid_snapshot", f"a periodic projection snapshot has kind {PERIODIC_KIND!r}")
        if data.get("contract") != CONTRACT:
            raise ProjectionError("unsupported_contract", f"this runtime reads contract {CONTRACT!r}, not "
                                  f"{data.get('contract')!r}")
        problems = shape_problems("periodic_projection", dict(data))
        if problems:
            raise ProjectionError("invalid_snapshot", "; ".join(problems))
        p = cls.build(nodes=data["nodes"], edges=data["edges"], dimensions=data["dimensions"],
                      periodic=data["periodic"], relations=data["relations"], node_types=data["node_types"],
                      bindings=data["bindings"], type_parents=data["type_parents"],
                      relation_parents=data["relation_parents"])
        if data["identity"] != p.identity:
            raise ProjectionError("identity_mismatch", f"the snapshot says {data['identity']}, its records give "
                                  f"{p.identity}")
        return p

    # ------------------------------------------------------------------------------------------------ reading
    @property
    def identity(self) -> str:
        return self._identity

    @property
    def cell(self) -> Projection:
        """The cell's records as a projection, shifts aside."""
        return self._cell

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def periodic(self) -> tuple[bool, ...]:
        return self._periodic

    @property
    def bindings(self) -> Mapping[str, str]:
        return self._cell.bindings

    @property
    def relations(self) -> tuple[str, ...]:
        return self._cell.relations

    @property
    def node_types(self) -> tuple[str, ...]:
        return self._cell.node_types

    @property
    def node_count(self) -> int:
        """Nodes of the cell."""
        return self._cell.node_count

    @property
    def edge_count(self) -> int:
        """Edges of the cell."""
        return self._cell.edge_count

    def shift(self, edge_id: str) -> tuple[int, ...]:
        return self._shifts[self._cell._edge_index[edge_id]]

    def image(self, node_id: str) -> tuple[str, tuple[int, ...]] | None:
        """(the cell's node, the image) that an image ID names in this projection, or None when it names none."""
        parsed = parse_image_id(node_id, self._dimensions)
        if parsed is None or parsed[0] not in self._cell._node_index:
            return None
        if any(x and not axis for x, axis in zip(parsed[1], self._periodic)):
            return None
        return parsed

    def stale_bindings(self, current: Mapping[str, str]) -> tuple[str, ...]:
        return self._cell.stale_bindings(current)

    def to_dict(self) -> dict:
        snap = self._cell.to_dict()
        for e in snap["edges"]:
            e["shift"] = list(self._shifts[self._cell._edge_index[e["id"]]])
        return {**snap, "kind": PERIODIC_KIND, "identity": self._identity, "dimensions": self._dimensions,
                "periodic": list(self._periodic)}

    def request(self, operation: str, **params: Any):
        from .request import normalize_request
        return normalize_request({"operation": operation, "projection": self._identity, **params})

    def __repr__(self) -> str:
        return (f"<PeriodicProjection {self._identity[:19]}… cell nodes={self.node_count} edges={self.edge_count} "
                f"dimensions={self._dimensions}>")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PeriodicProjection) and other._identity == self._identity

    def __hash__(self) -> int:
        return hash(self._identity)


def _identity(p: PeriodicProjection) -> str:
    """sha256 over newline-terminated canonical JSON lines: a header (the kind, the contract, the dimensions, the
    periodic axes, the bindings, both vocabularies, both hierarchies and the counts), then one ``[id, [types]]`` line
    per node and one ``[id, source, target, relation, [shift]]`` line per edge, each in ascending ID order. Any
    implementation can reproduce it from the records alone."""
    cell = p._cell
    h = hashlib.sha256()
    header = {"kind": PERIODIC_KIND, "contract": CONTRACT, "dimensions": p._dimensions,
              "periodic": list(p._periodic), "bindings": dict(cell.bindings), "relations": list(cell.relations),
              "node_types": list(cell.node_types),
              "type_parents": {k: list(v) for k, v in cell.type_parents.items()},
              "relation_parents": {k: list(v) for k, v in cell.relation_parents.items()},
              "node_count": cell.node_count, "edge_count": cell.edge_count}
    h.update(canonical_json(header).encode("utf-8") + b"\n")
    enc, lines = encode_text, []
    for n in cell.nodes():
        lines.append(f"[{enc(n.id)},[{','.join(map(enc, n.types))}]]")
    for ei, e in enumerate(cell.edges()):
        shift = ",".join(map(str, p._shifts[ei]))
        lines.append(f"[{enc(e.id)},{enc(e.source)},{enc(e.target)},{enc(e.relation)},[{shift}]]")
    h.update(("".join(line + "\n" for line in lines)).encode("utf-8"))
    out = "sha256:" + h.hexdigest()
    assert is_digest(out)
    return out


# ---------------------------------------------------------------------------------------------- searching
def _bind(p: PeriodicProjection, req: Any) -> SimpleNamespace:
    """A request checked against the cell (its scope, through the cell's projection) and its nodes' images."""
    from .engine import _scope
    from .request import FAILED, UNSUPPORTED
    if req.operation not in PERIODIC_OPERATIONS:
        raise RequestError(UNSUPPORTED, "unsupported_operation", f"{req.operation} does not run on a periodic "
                           f"projection (it runs {', '.join(PERIODIC_OPERATIONS)})",
                           {"operation": req.operation, "projection": "periodic"})
    field = {"dependency_impact": "changed", "periodicity": "nodes"}.get(req.operation, "sources")
    cell = p._cell
    sources, missing = [], []
    for nid in req[field]:
        parsed = p.image(nid)
        if parsed is None:
            missing.append(nid)
        else:
            sources.append((cell._node_index[parsed[0]], parsed[1]))
    if missing:
        raise RequestError(FAILED, "unknown_node", f"{field} names {len(missing)} node(s) that are no image of the "
                           f"cell's nodes (<node>@<k1>,...,<k{p._dimensions}>): {missing[:10]}",
                           {"field": field, "nodes": missing[:100]})
    plan = SimpleNamespace(max_results=req.limits.get("max_results"), max_depth=req._data.get("max_depth"),
                           sources=sources)
    plan.fwd, plan.rev = _scope(cell, req["scope"], "scope")
    if req.operation == "periodicity" and plan.fwd != plan.rev:
        raise RequestError(FAILED, "invalid_request", "periodicity follows its scope both ways: give each relation "
                           "forward and reverse", {"field": "scope"})
    return plan


def _hops(p: PeriodicProjection, sides: tuple, key: tuple, text: str) -> list[tuple[str, int, tuple, int]]:
    """The hops in scope from the image ``key`` = (node, image) whose ID is ``text``, in canonical order: its forward
    hops (images of the edges it is the source of) by lifted edge ID, then its reverse hops by lifted edge ID. Each is
    (lifted edge ID, cell edge, the image it leads to, 0 forward or 1 reverse)."""
    cell, shifts = p._cell, p._shifts
    eids, rel = cell._edge_ids, cell._edge_rel
    u, k = key
    at = text[text.rindex("@"):]
    out: list[tuple[str, int, tuple, int]] = []
    for lists, base, marks, ends, flag in sides:
        hs = lists[u]
        if hs is None:
            hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
        side = []
        for ei in hs:
            s = shifts[ei]
            if flag == 0:                                     # this image is the edge's source
                side.append((eids[ei] + at, ei, (ends[ei], tuple(a + b for a, b in zip(k, s))), 0))
            else:                                             # the edge's image whose target this is
                j = tuple(a - b for a, b in zip(k, s))
                side.append((image_id(eids[ei], j), ei, (ends[ei], j), 1))
        side.sort()
        out.extend(side)
    return out


def _search_bfs(p: PeriodicProjection, plan: SimpleNamespace, ctl: Any, strict: bool) -> SimpleNamespace:
    """The engine's breadth-first search (:func:`graph_runtime.engine._search_bfs`) on the infinite periodic graph:
    the same order, depths, witnesses, limits and counters, over images (node, image) named by their image IDs,
    reading each image's hops when it is expanded."""
    from .engine import _sides
    cell = p._cell
    names: dict[tuple, str] = {}

    def name(key: tuple) -> str:
        text = names.get(key)
        if text is None:
            text = names[key] = image_id(cell._node_ids[key[0]], key[1])
        return text

    st = SimpleNamespace(order=[], depth={}, parent={}, examined=0, discovered=0, interruption=None, name=name)
    order, depth, parent = st.order, st.depth, st.parent
    cap, max_depth = plan.max_results, plan.max_depth
    level: dict[tuple, int] = {}
    queue: deque = deque()
    for s in sorted(plan.sources, key=name):                # sources in ascending image ID
        level[s] = 0
        queue.append(s)
        if not strict:
            if len(order) >= cap:
                st.discovered = len(plan.sources)
                st.interruption = "output_limit"
                return st
            depth[s] = 0
            order.append(s)
    sides = _sides(cell, plan.fwd, plan.rev)
    examined, full = 0, False
    while queue and not full:
        u = queue.popleft()
        d = level[u]
        if max_depth is not None and d >= max_depth:
            continue
        why = ctl.stop()
        if why:
            st.interruption = why
            break
        hops = _hops(p, sides, u, name(u))
        for n, (_, ei, v, flag) in enumerate(hops):
            examined += 1
            if v in depth:
                continue
            if len(order) >= cap:
                full = True
                break
            depth[v] = d + 1
            parent[v] = (u, ei, flag, hops[n][0])
            order.append(v)
            if v not in level:
                level[v] = d + 1
                queue.append(v)
        if full:
            st.interruption = "output_limit"
    st.examined, st.discovered = examined, len(level)
    return st


def _answer_bfs(p: PeriodicProjection, plan: SimpleNamespace, st: SimpleNamespace, strict: bool):
    from .engine import _DIRECTION, WITNESS_NOTE
    cell, name = p._cell, st.name
    items = []
    for v in sorted(st.order, key=name):
        par = st.parent.get(v)
        via = None
        if par is not None:
            u, ei, flag, lifted = par
            via = {"edge": lifted, "relation": cell._relations[cell._edge_rel[ei]], "direction": _DIRECTION[flag],
                   "from": name(u)}
        items.append({"node": name(v), "depth": st.depth[v], "via": via})
    if strict:
        answer = {"changed": sorted(name(s) for s in plan.sources), "affected": items, "count": len(items)}
    else:
        answer = {"reached": items, "count": len(items)}
    return answer, ([WITNESS_NOTE] if st.parent else [])


# ---------------------------------------------------------------------------------------------- periodicity
def _search_periodicity(p: PeriodicProjection, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """The translation lattice of the component of each requested image, by the classic analysis of a periodic net's
    quotient graph: a breadth-first spanning tree of the cell's nodes the component meets, from the image's node,
    gives each of them the image the tree reaches (its potential); every edge of the component then closes a cycle
    whose net shift ``p(source) + shift - p(target)`` lies in the lattice, and these shifts generate it. Its Hermite
    normal form is the lattice's canonical basis, each row written as a combination of the cycles (the witness), and
    its rank says how the component repeats: 0 finite, 1 a chain, 2 a layer, 3 a framework. One tree serves every
    image of a node it covers: an image whose offset from the tree's lies outside the lattice is another, translated
    copy of the component (an interpenetrating one when the offset lies in the lattice's span: ``copies`` counts
    those)."""
    from .engine import _sides
    from .lattice import hermite_combinations, reduced
    cell, shifts, dims = p._cell, p._shifts, p._dimensions
    rel, src, dst = cell._edge_rel, cell._edge_src, cell._edge_dst
    sides = _sides(cell, plan.fwd, plan.rev)
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, analyses=[], components=[])
    analysis_of: dict[int, int] = {}                      # cell node -> its analysis
    component_of: dict[tuple, Any] = {}                   # (analysis, offset's coset representative) -> component
    examined = 0
    for seed in sorted(plan.sources, key=lambda key: image_id(cell._node_ids[key[0]], key[1])):
        u, k = seed
        if u not in analysis_of:
            potential: dict[int, tuple] = {u: (0,) * dims}
            via: dict[int, tuple | None] = {u: None}
            queue = deque([u])
            stopped = False
            while queue:
                a = queue.popleft()
                why = ctl.stop()
                if why:
                    st.interruption, stopped = why, True
                    break
                for lists, base, marks, ends, flag in sides:
                    hs = lists[a]
                    if hs is None:
                        hs = lists[a] = [ei for ei in base[a] if marks[rel[ei]]]
                    examined += len(hs)
                    for ei in hs:
                        b = ends[ei]
                        if b not in potential:
                            s = shifts[ei]
                            potential[b] = tuple(x + y if flag == 0 else x - y for x, y in zip(potential[a], s))
                            via[b] = (a, ei, flag)
                            queue.append(b)
            if stopped:
                break
            members = sorted(potential)
            cycles, cycle_edges = [], []
            for ei in sorted(ei for m in members for ei in cell._out[m] if plan.fwd[rel[ei]]):
                z = tuple(a + s - b for a, s, b in zip(potential[src[ei]], shifts[ei], potential[dst[ei]]))
                if any(z):                                # every edge of the component, once, from source to target
                    cycles.append(z)
                    cycle_edges.append(ei)
            why: list[str] = []

            def halt() -> str | None:                     # each row operation of the reduction is one expansion
                reason = ctl.stop()
                if reason:
                    why.append(reason)
                return reason
            hnf = hermite_combinations(cycles, dims, halt)
            if hnf is None:
                st.interruption = why[0]
                break
            basis, combos = hnf
            analysis_of.update((m, len(st.analyses)) for m in members)
            st.analyses.append(SimpleNamespace(root=u, members=members, potential=potential, via=via, basis=basis,
                                               combos=combos, cycle_edges=cycle_edges))
        a = st.analyses[analysis_of[u]]
        offset = tuple(x - y for x, y in zip(k, a.potential[u]))   # the tree's root image this seed implies
        key = (analysis_of[u], reduced(a.basis, offset))  # equal exactly when the offsets differ by a translation
        comp = component_of.get(key)
        if comp is not None:
            comp.seeds.append(seed)
        else:
            component_of[key] = SimpleNamespace(analysis=a, offset=offset, seeds=[seed])
            st.components.append(component_of[key])
    st.examined = examined
    st.discovered = sum(len(a.members) for a in st.analyses)
    return st


def _answer_periodicity(p: PeriodicProjection, plan: SimpleNamespace, st: SimpleNamespace):
    from .engine import _DIRECTION, WITNESS_NOTE
    from .lattice import copies
    cell, ids, eids = p._cell, p._cell._node_ids, p._cell._edge_ids
    components = []
    for comp in st.components:
        a, off = comp.analysis, comp.offset

        def at(v: int) -> tuple:
            return tuple(x + y for x, y in zip(a.potential[v], off))
        nodes = []
        for v in a.members:
            par = a.via[v]
            hop = None
            if par is not None:                           # named by the image of the edge that the tree follows
                u, ei, flag = par
                lifted = image_id(eids[ei], at(u) if flag == 0 else at(v))
                hop = {"edge": lifted, "relation": cell._relations[cell._edge_rel[ei]], "direction": _DIRECTION[flag],
                       "from": image_id(ids[u], at(u))}
            nodes.append({"node": ids[v], "image": list(at(v)), "via": hop})
        components.append({
            "seeds": sorted(image_id(ids[u], k) for u, k in comp.seeds),
            "nodes": nodes, "rank": len(a.basis), "basis": [list(row) for row in a.basis],
            "copies": copies(a.basis),
            "cycles": [{"edge": eids[ei], "net": list(z)} for ei, z in zip(a.cycle_edges, (
                tuple(x + s - y for x, s, y in zip(a.potential[cell._edge_src[ei]], p._shifts[ei],
                                                     a.potential[cell._edge_dst[ei]])) for ei in a.cycle_edges))],
            "witnesses": [[{"edge": eids[a.cycle_edges[j]], "times": c} for j, c in enumerate(comb) if c]
                          for comb in a.combos]})
    components.sort(key=lambda c: c["seeds"][0])
    return {"components": components, "count": len(components)}, ([WITNESS_NOTE] if components else [])


_OPERATIONS = {"reachability": (lambda p, plan, ctl: _search_bfs(p, plan, ctl, False),
                                lambda p, plan, st: _answer_bfs(p, plan, st, False)),
               "dependency_impact": (lambda p, plan, ctl: _search_bfs(p, plan, ctl, True),
                                     lambda p, plan, st: _answer_bfs(p, plan, st, True)),
               "periodicity": (_search_periodicity, _answer_periodicity)}


# ---------------------------------------------------------------------------------------------- checking
class PeriodicRecords:
    """The infinite graph a periodic projection's records stand for, read lazily for checking results
    (:func:`graph_runtime.verify_result`): ``edge_of[lifted edge ID]`` gives (source image, target image, relation),
    ``types_of[image ID]`` the declared types of the node it images, and :meth:`hops_into_scope` enumerates an image's
    hops. Plain lookups over the records; nothing from the engine."""

    def __init__(self, nodes: Iterable[Any], edges: Iterable[Mapping], dimensions: int | None = None):
        self.nodes = {}
        for n in nodes:
            self.nodes[n if isinstance(n, str) else n["id"]] = set() if isinstance(n, str) else set(
                n.get("types") or ())
        self.edges = {e["id"]: (e["source"], e["target"], e["relation"], tuple(e["shift"])) for e in edges}
        if dimensions is None:
            dimensions = len(next(iter(self.edges.values()))[3]) if self.edges else 0
        self.dimensions = dimensions
        self.out: dict = {}
        self.into: dict = {}
        for eid, (src, dst, _, _) in self.edges.items():
            self.out.setdefault(src, []).append(eid)
            self.into.setdefault(dst, []).append(eid)

    def edge(self, lifted: Any) -> tuple[str, str, str] | None:
        parsed = parse_image_id(lifted, self.dimensions)
        if parsed is None or parsed[0] not in self.edges:
            return None
        src, dst, rel, shift = self.edges[parsed[0]]
        k = parsed[1]
        return image_id(src, k), image_id(dst, tuple(a + b for a, b in zip(k, shift))), rel

    def types(self, node: Any) -> set | None:
        parsed = parse_image_id(node, self.dimensions)
        if parsed is None or parsed[0] not in self.nodes:
            return None
        return self.nodes[parsed[0]]

    def hops(self, node: Any) -> list[tuple[str, str, str, str]]:
        """(lifted edge ID, the image it leads to, the relation, forward or reverse) for every hop at an image."""
        parsed = parse_image_id(node, self.dimensions)
        if parsed is None:
            return []
        u, k = parsed
        out = []
        for eid in self.out.get(u, ()):
            _, dst, rel, shift = self.edges[eid]
            out.append((image_id(eid, k), image_id(dst, tuple(a + b for a, b in zip(k, shift))), rel, "forward"))
        for eid in self.into.get(u, ()):
            src, _, rel, shift = self.edges[eid]
            j = tuple(a - b for a, b in zip(k, shift))
            out.append((image_id(eid, j), image_id(src, j), rel, "reverse"))
        return out


class _LiftedEdges(Mapping):
    """``edge_of`` for the checker: a lifted edge ID -> (source image, target image, relation)."""

    def __init__(self, records: PeriodicRecords):
        self.records = records

    def __getitem__(self, key: Any) -> tuple:
        out = self.records.edge(key)
        if out is None:
            raise KeyError(key)
        return out

    def __iter__(self):
        raise TypeError("the edges of an infinite periodic graph are not listed")

    def __len__(self) -> int:
        raise TypeError("an infinite periodic graph has no edge count")


class _ImageTypes(Mapping):
    """``types_of`` for the checker: an image ID -> the types of the node it images (with their ancestors)."""

    def __init__(self, records: PeriodicRecords, up: Mapping[str, set]):
        self.records, self.up = records, up

    def __getitem__(self, key: Any) -> set:
        declared = self.records.types(key)
        if declared is None:
            raise KeyError(key)
        return declared.union(*(self.up.get(t, ()) for t in declared))

    def __iter__(self):
        raise TypeError("the nodes of an infinite periodic graph are not listed")

    def __len__(self) -> int:
        raise TypeError("an infinite periodic graph has no node count")


def periodic_view(projection: PeriodicProjection) -> MappingProxyType:
    """The records of a periodic projection, as :func:`graph_runtime.verify_result` takes them."""
    snap = projection.to_dict()
    return MappingProxyType({"nodes": snap["nodes"], "edges": snap["edges"], "type_parents": snap["type_parents"],
                             "relation_parents": snap["relation_parents"], "dimensions": snap["dimensions"]})
