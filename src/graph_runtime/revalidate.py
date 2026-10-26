"""Revalidation: whether a result still holds on another projection, decided from its answer and certificate, without
searching again.

A result answers its request on the projection it was made for. When the records change, so does the projection,
and the result is stale. Often it still holds: an edit away from what the result reached changes nothing it says.
What makes an answer checkable is what :func:`graph_runtime.verify_result` checks: witnesses, the closure of a
complete reachability or impact answer, and certificates (``certificate: true``). :func:`revalidate` checks them
against the new projection, in time proportional to the answer. Given the difference between the two projections
(:func:`diff`), it checks only what the edit touches, in time proportional to the edit, so one diff serves every
cached result (:func:`revalidate_all`).

The verdict has three values:

* ``holds``: the answer is still a correct answer to the same request on the new projection. Its witnesses are
  hops of the new projection, and its completeness, negative or bound still follows from its answer and
  certificate. A new search could choose other witnesses among equally good ones.
* ``fails``: it is not. The reasons name what refutes it: a witness edge or a listed node that is gone, a node whose
  types no longer fit, a hop from an expanded node to a node the answer does not list (or lists deeper), or a node,
  relation, type or cost the request names that the new projection lacks.
* ``unknown``: neither can be shown without a new search. This covers an interrupted answer, an operation without
  certificates (pattern, support), a negative or an optimum without its certificate, and a certificate an edit
  broke without refuting the answer.

Diff-based revalidation trusts that the result was correct on its own projection (as made by :func:`execute`, or
checked by :func:`verify_result`); revalidation without a diff checks everything against the new projection.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable, Mapping
from fractions import Fraction
from types import MappingProxyType, SimpleNamespace
from typing import Any

from ._canonical import canonical_json, digest, freeze, thaw
from .engine import _back_hops, _bind, _hops
from .errors import RequestError
from .projection import CONTRACT, Projection
from .request import normalize_request
from .result import COMPLETED, Result

HOLDS, FAILS, UNKNOWN = "holds", "fails", "unknown"
VERDICTS = (HOLDS, FAILS, UNKNOWN)
REVALIDATION_KIND = "graph_runtime.revalidation"
MAX_REASONS = 100
_REFUTING = {"node_gone", "witness_gone", "type_changed", "reaches_unlisted", "reaches_shallower", "unanswerable"}


class ProjectionDiff:
    """What changed from one projection to another, by original IDs: nodes added, removed or retyped (their declared
    types changed), and edges added or removed. An edge whose ends or relation changed is removed and added. The
    vocabulary (relations, node types and both hierarchies) either stayed or changed as a whole."""

    __slots__ = ("old", "new", "added_nodes", "removed_nodes", "retyped_nodes", "added_edges", "removed_edges",
                 "removed_ends", "vocabulary_changed")

    def __init__(self, **fields: Any) -> None:
        for k in self.__slots__:
            object.__setattr__(self, k, fields[k])

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a ProjectionDiff is immutable")

    @property
    def size(self) -> int:
        return (len(self.added_nodes) + len(self.removed_nodes) + len(self.retyped_nodes) + len(self.added_edges)
                + len(self.removed_edges))

    def to_dict(self) -> dict:
        out = {k: (sorted(getattr(self, k)) if isinstance(getattr(self, k), frozenset) else getattr(self, k))
               for k in self.__slots__}
        out["removed_ends"] = {e: list(v) for e, v in sorted(self.removed_ends.items())}
        return out

    def __repr__(self) -> str:
        return (f"<ProjectionDiff +{len(self.added_nodes)}/-{len(self.removed_nodes)}/~{len(self.retyped_nodes)} "
                f"nodes, +{len(self.added_edges)}/-{len(self.removed_edges)} edges>")


def diff(old: Projection, new: Projection) -> ProjectionDiff:
    """The difference between two projections, in one pass over each."""
    if not isinstance(old, Projection) or not isinstance(new, Projection):
        raise TypeError("diff compares two Projections")
    vocabulary = (old.relations != new.relations or old.node_types != new.node_types
                  or old.type_parents != new.type_parents or old.relation_parents != new.relation_parents)
    on, nn = old._node_index, new._node_index
    removed_nodes = frozenset(n for n in on if n not in nn)
    added_nodes = frozenset(n for n in nn if n not in on)
    ot, nt = old._type_index, new._type_index
    otypes, ntypes = old.node_types, new.node_types

    def names(mask: int, types: tuple) -> frozenset:
        return frozenset(t for i, t in enumerate(types) if mask >> i & 1)

    retyped = frozenset(n for n, i in on.items() if n in nn and (
        old._declared_mask[i] != new._declared_mask[nn[n]] if ot == nt
        else names(old._declared_mask[i], otypes) != names(new._declared_mask[nn[n]], ntypes)))

    def ends(p: Projection, ei: int) -> tuple[str, str, str]:
        return p._node_ids[p._edge_src[ei]], p._node_ids[p._edge_dst[ei]], p._relations[p._edge_rel[ei]]

    oe, ne = old._edge_index, new._edge_index
    removed_ends = {e: ends(old, i) for e, i in oe.items() if e not in ne or ends(old, i) != ends(new, ne[e])}
    removed_edges = frozenset(removed_ends)
    added_edges = frozenset(e for e in ne if e not in oe or e in removed_edges)
    return ProjectionDiff(old=old.identity, new=new.identity, added_nodes=added_nodes, removed_nodes=removed_nodes,
                          retyped_nodes=retyped, added_edges=added_edges, removed_edges=removed_edges,
                          removed_ends=MappingProxyType(removed_ends), vocabulary_changed=vocabulary)


class Revalidation:
    """A revalidation record: which result, from which projection to which, the verdict, and its reasons (at most
    ``MAX_REASONS``, in canonical order, with how many there were). ``revalidation_id`` is the digest of the rest;
    ``checked`` (``answer`` or ``diff``) says how it was decided and is not part of it."""

    __slots__ = ("_data", "checked")

    def __init__(self, data: dict, checked: str) -> None:
        object.__setattr__(self, "_data", freeze(data))
        object.__setattr__(self, "checked", checked)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a Revalidation is immutable")

    @property
    def verdict(self) -> str:
        return self._data["verdict"]

    @property
    def holds(self) -> bool | None:
        """True (holds), False (fails) or None (unknown)."""
        return {HOLDS: True, FAILS: False}.get(self.verdict)

    @property
    def reasons(self) -> tuple:
        return self._data["reasons"]

    @property
    def revalidation_id(self) -> str:
        return self._data["revalidation_id"]

    def to_dict(self) -> dict:
        return thaw(self._data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __repr__(self) -> str:
        return f"<Revalidation {self._data['operation']} {self.verdict} ({len(self.reasons)} reasons)>"


def revalidate(result: Result | Mapping, projection: Projection, *, diff: ProjectionDiff | None = None) -> Revalidation:
    """Whether ``result`` still holds on ``projection``. With ``diff`` (from the result's projection to this one),
    only what the edit touches is checked."""
    data = result._data if isinstance(result, Result) else result       # read in place, never changed
    if not isinstance(projection, Projection):
        raise TypeError("revalidate checks a result against a Projection")
    reasons, checked = _decide(data, projection, diff)
    reasons = sorted({canonical_json(r): r for r in reasons}.values(), key=canonical_json)
    verdict = (FAILS if any(r["code"] in _REFUTING for r in reasons) else UNKNOWN if reasons else HOLDS)
    body = {"kind": REVALIDATION_KIND, "contract": CONTRACT, "operation": data.get("operation"),
            "result_id": data.get("result_id"), "request_id": data.get("request_id"),
            "from_projection": data.get("projection"), "to_projection": projection.identity, "verdict": verdict,
            "reasons": reasons[:MAX_REASONS], "reason_count": len(reasons)}
    body["revalidation_id"] = digest(body)
    return Revalidation(body, checked)


def revalidate_all(results: Iterable[Result | Mapping], old: Projection, new: Projection) -> list[Revalidation]:
    """Every result made on ``old``, revalidated on ``new`` with one diff between them."""
    d = diff(old, new)
    return [revalidate(r, new, diff=d) for r in results]


# ---------------------------------------------------------------------------------------------- deciding
def _decide(data: dict, p: Projection, d: ProjectionDiff | None) -> tuple[list[dict], str]:
    """The reasons against the result, and how they were found: ``diff`` when a diff from the result's projection
    to this one, over the same vocabulary, was given and trusted; ``answer`` otherwise."""
    use = d is not None and d.old == data.get("projection") and d.new == p.identity and not d.vocabulary_changed
    checked = "diff" if use else "answer"
    if data.get("status") != COMPLETED:
        return [{"code": "not_complete", "status": data.get("status")}], checked
    if data.get("projection") == p.identity:
        return [], checked
    op = data["operation"]
    if op not in ("reachability", "dependency_impact", "typed_path", "shortest_path", "bottleneck_path",
                  "regular_path"):
        return [{"code": "not_certified", "operation": op}], checked
    try:
        req = normalize_request({**data["request"], "projection": p.identity})
        plan = (_bind_costs_of_edit(p, req, d) if use and op in ("shortest_path", "bottleneck_path")
                else _bind(p, req))
    except RequestError as e:
        return [{"code": "unanswerable", "error": e.code}], checked
    ctx = SimpleNamespace(p=p, plan=plan, request=data["request"], answer=data["answer"], diff=d if use else None,
                          out=[])
    if op in ("reachability", "dependency_impact"):
        _sets(ctx, strict=op == "dependency_impact")
    elif op == "typed_path":
        _typed(ctx)
    elif op == "shortest_path":
        _shortest(ctx)
    elif op == "regular_path":
        _regular(ctx)
    else:
        _bottleneck(ctx)
    return ctx.out, checked


def _bind_costs_of_edit(p: Projection, req: Any, d: ProjectionDiff) -> SimpleNamespace:
    """A shortest or bottleneck path request bound to the new projection for a diff: what binding checks and needs,
    read from the edit alone. The request's nodes must still exist, an edge its by_edge table names must not be gone,
    and every added edge in scope needs a cost (or weight); only the added edges' are computed (binding the whole
    table reads every edge in scope)."""
    from .engine import _nodes, _scope
    plan = SimpleNamespace(sources=_nodes(p, req["sources"], "sources"),
                           targets=set(_nodes(p, req["targets"], "targets")))
    plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
    weights = req.operation == "bottleneck_path"
    (kind, table), = req["weights" if weights else "costs"].items()
    plan.rational = not weights and any(isinstance(x, float) for x in table.values())
    if weights:
        plan.objective = req["objective"]
    if kind == "by_edge":
        gone = sorted(e for e in table if e in d.removed_edges and e not in d.added_edges)
        if gone:
            raise RequestError("failed", "unknown_edge", f"costs.by_edge names edges that are gone: {gone[:10]}")
    plan.cost = {}
    for eid in d.added_edges:
        ei = p._edge_index[eid]
        rel = p._edge_rel[ei]
        if plan.fwd[rel] or plan.rev[rel]:
            c = table.get(p._relations[rel] if kind == "by_relation" else eid)
            if c is None:
                raise RequestError("failed", "missing_cost", f"the added edge {eid} in scope has no cost")
            plan.cost[ei] = Fraction(c) if plan.rational else c
    plan.weight = plan.cost
    return plan


def _edge(p: Projection, eid: str) -> int | None:
    return p._edge_index.get(eid)


def _lookup(items: Any, key: Callable[[Any], Any], in_diff: bool) -> Callable[[Any], Any]:
    """Find an item of an answer list by its key. Every such list is in canonical (ascending ID) order, so with a diff
    a binary search finds it without reading the whole answer; without one, a dict made once."""
    if in_diff:
        def find(value: Any) -> Any:
            i = bisect_left(items, value, key=key)
            return items[i] if i < len(items) and key(items[i]) == value else None
        return find
    table = {key(x): x for x in items}
    return table.get


def _hop_ok(ctx: SimpleNamespace, hop: Mapping, fwd: list[bool], rev: list[bool]) -> bool:
    """Whether a witness hop is a hop of the new projection: its edge, with the same ends and relation, in scope in
    its direction. With a diff, an edge that is neither removed nor changed is still one."""
    if ctx.diff is not None:
        return hop["edge"] not in ctx.diff.removed_edges
    p = ctx.p
    ei = _edge(p, hop["edge"])
    if ei is None or p._relations[p._edge_rel[ei]] != hop["relation"]:
        return False
    src, dst = p._node_ids[p._edge_src[ei]], p._node_ids[p._edge_dst[ei]]
    if hop["direction"] == "forward":
        return (src, dst) == (hop["from"], hop["to"]) and fwd[p._edge_rel[ei]]
    return (dst, src) == (hop["from"], hop["to"]) and rev[p._edge_rel[ei]]


def _gone(ctx: SimpleNamespace, node: str) -> bool:
    if ctx.diff is not None:
        return node in ctx.diff.removed_nodes
    return node not in ctx.p._node_index


def _added_hops(ctx: SimpleNamespace, fwd: list[bool], rev: list[bool]):
    """(edge, from, to) for every hop in scope that an added edge makes, by node index of the new projection."""
    p = ctx.p
    for eid in sorted(ctx.diff.added_edges):
        ei = p._edge_index[eid]
        rel = p._edge_rel[ei]
        if fwd[rel]:
            yield eid, p._edge_src[ei], p._edge_dst[ei]
        if rev[rel]:
            yield eid, p._edge_dst[ei], p._edge_src[ei]


# ---------------------------------------------------------------------------------------------- reachability, impact
def _sets(ctx: SimpleNamespace, strict: bool) -> None:
    p, plan, request, answer, out, d = ctx.p, ctx.plan, ctx.request, ctx.answer, ctx.out, ctx.diff
    entries = answer["affected" if strict else "reached"]
    listed = _lookup(entries, lambda e: e["node"], d is not None)
    seeds = request["changed" if strict else "sources"]
    is_seed = _lookup(seeds, lambda x: x, d is not None)
    if d is not None:                                        # what the edit removed, against the answer
        for n in sorted(d.removed_nodes):
            if listed(n) is not None:
                out.append({"code": "node_gone", "node": n})
        for eid in sorted(d.removed_edges):
            src, dst, _ = d.removed_ends[eid]
            for n in sorted({src, dst}):
                e = listed(n)
                if e is not None and e["via"] is not None and e["via"]["edge"] == eid and n not in d.removed_nodes:
                    out.append({"code": "witness_gone", "edge": eid, "node": n})
    else:
        for e in entries:
            if _gone(ctx, e["node"]):
                out.append({"code": "node_gone", "node": e["node"]})
            elif e["via"] is not None and not _hop_ok(ctx, {**e["via"], "to": e["node"]}, plan.fwd, plan.rev):
                out.append({"code": "witness_gone", "edge": e["via"]["edge"], "node": e["node"]})
    max_depth = request["max_depth"]
    ids = p._node_ids

    def expanded(n: str) -> int | None:
        """The depth a node was expanded at: a changed node at 0, even when a cycle lists it deeper."""
        if is_seed(n) is not None:
            return 0
        e = listed(n)
        return None if e is None else e["depth"]

    def check(eid: str, a: int, b: int) -> None:
        da = expanded(ids[a])
        if da is None or (max_depth is not None and da >= max_depth):
            return
        e = listed(ids[b])
        if e is None:
            out.append({"code": "reaches_unlisted", "edge": eid, "from": ids[a], "to": ids[b]})
        elif e["depth"] > da + 1:
            out.append({"code": "reaches_shallower", "edge": eid, "from": ids[a], "to": ids[b]})

    if d is not None:
        for eid, a, b in _added_hops(ctx, plan.fwd, plan.rev):
            check(eid, a, b)
        return
    for node in dict.fromkeys([*seeds, *(e["node"] for e in entries)]):
        u = p._node_index.get(node)
        if u is not None:
            for ei, v, _ in _hops(p, u, plan.fwd, plan.rev):
                check(p._edge_ids[ei], u, v)


# ---------------------------------------------------------------------------------------------- typed path
def _typed(ctx: SimpleNamespace) -> None:
    p, plan, answer, out, d = ctx.p, ctx.plan, ctx.answer, ctx.out, ctx.diff
    if answer["found"]:
        for i, hop in enumerate(answer["witness"]["hops"]):
            fwd, rev, mask = plan.positions[i]
            if _gone(ctx, hop["to"]):
                out.append({"code": "node_gone", "node": hop["to"]})
            elif not _hop_ok(ctx, hop, fwd, rev):
                out.append({"code": "witness_gone", "edge": hop["edge"]})
            elif mask is not None and (d is None or hop["to"] in d.retyped_nodes) \
                    and not p._node_mask[p._node_index[hop["to"]]] & mask:
                out.append({"code": "type_changed", "node": hop["to"], "position": i})
        return
    cert = answer["certificate"]
    if cert is None:
        out.append({"code": "no_certificate"})
        return
    state = _lookup(cert["states"], lambda st: (st["position"], st["node"]), d is not None)
    last, targets, ids = len(plan.positions), plan.targets, p._node_ids

    def check(eid: str, a: int, b: int, i: int) -> None:
        """A hop the pattern allows from state (a, i) to b must lead to a listed state, or end at a non-target."""
        fwd, rev, mask = plan.positions[i]
        if state((i, ids[a])) is None or (mask is not None and not p._node_mask[b] & mask):
            return
        if (i + 1 < last and state((i + 1, ids[b])) is None) or \
                (i + 1 == last and (targets is None or b in targets)):
            out.append({"code": "certificate_broken", "edge": eid, "from": ids[a], "to": ids[b], "position": i})

    if d is None:
        for st in cert["states"]:
            u = p._node_index.get(st["node"])
            if u is not None:
                fwd, rev, _ = plan.positions[st["position"]]
                for ei, v, _ in _hops(p, u, fwd, rev):
                    check(p._edge_ids[ei], u, v, st["position"])
        return
    for i, (fwd, rev, _) in enumerate(plan.positions):
        for eid, a, b in _added_hops(ctx, fwd, rev):
            check(eid, a, b, i)
        for node in sorted(d.retyped_nodes):                 # a node newly admitted at a position
            b = p._node_index[node]
            for ei, a, _ in _back_hops(p, b, fwd, rev):
                check(p._edge_ids[ei], a, b, i)


# ---------------------------------------------------------------------------------------------- regular path
def _regular(ctx: SimpleNamespace) -> None:
    """A regular path answer: its witness (or its tree's hops) must still be hops of the new projection, each in its
    step's scope and landing on its step's types. What it claims beyond them, the fewest hops, a negative or a
    complete reached set, rests on its certificate: every move the edit adds from a state the certificate lists
    (below its bound) must lead to a listed state no further, and must not end a match the answer does not give.
    An edit that breaks the certificate leaves the answer unknown: a listed state carries no witness of its own."""
    p, plan, answer, out, d = ctx.p, ctx.plan, ctx.answer, ctx.out, ctx.diff
    auto, step_of, ids = plan.auto, plan.step_of, p._node_ids
    witness = plan.answer == "witness"

    def check_hop(hop: Mapping, state: int, to: str) -> None:
        fwd, rev, mask = step_of[state]
        if _gone(ctx, to):
            out.append({"code": "node_gone", "node": to})
        elif not _hop_ok(ctx, {**hop, "to": to}, fwd, rev):
            out.append({"code": "witness_gone", "edge": hop["edge"]})
        elif mask is not None and (d is None or to in d.retyped_nodes) \
                and not p._node_mask[p._node_index[to]] & mask:
            out.append({"code": "type_changed", "node": to, "state": state})

    if witness:
        hops = answer["witness"]["hops"] if answer["found"] else []
        for hop in hops:
            check_hop(hop, hop["state"], hop["to"])
        if answer["found"] and not hops:
            return                                        # a walk with no hop: nothing is shorter
    else:
        for t in answer["tree"]:
            if t["via"] is not None:
                check_hop(t["via"], t["state"], t["node"])
            elif _gone(ctx, t["node"]):
                out.append({"code": "node_gone", "node": t["node"]})
    cert = answer["certificate"]
    if cert is None:
        out.append({"code": "no_certificate"})
        return
    bound, targets = cert["bound"], plan.targets
    state = _lookup(cert["states"], lambda st: (st["state"], st["node"]), d is not None)
    reached = {} if witness else {e["node"]: e["length"] for e in answer["reached"]}
    before = [[] for _ in auto.steps]                     # the states each state follows
    for q, moves in enumerate(auto.follow):
        for i in moves:
            before[i].append(q)

    def check(eid: str, a: int, b: int, i: int) -> None:
        """A move into state i by the hop from a to b (in step i's scope): from every listed state (a, q) below the
        bound that state i follows, it must reach a listed state no further, and not end a match the answer lacks."""
        mask = step_of[i][2]
        if mask is not None and not p._node_mask[b] & mask:
            return
        for q in before[i]:
            st = state((q, ids[a]))
            if st is None or (bound is not None and st["length"] + 1 >= bound):
                continue
            n = st["length"] + 1
            nxt = state((i, ids[b]))
            ends = i in auto.accepting and (targets is None or b in targets)
            if nxt is None or nxt["length"] > n or (ends and (witness or reached.get(ids[b], n + 1) > n)):
                out.append({"code": "certificate_broken", "edge": eid, "from": ids[a], "to": ids[b], "state": i})
                return

    if d is None:
        for st in cert["states"]:
            u = p._node_index.get(st["node"])
            if u is None or (bound is not None and st["length"] + 1 >= bound):
                continue
            for i in auto.follow[st["state"]]:
                fwd, rev, _ = step_of[i]
                for ei, v, _ in _hops(p, u, fwd, rev):
                    check(p._edge_ids[ei], u, v, i)
        return
    for i in range(1, len(auto.steps)):
        fwd, rev, _ = step_of[i]
        for eid, a, b in _added_hops(ctx, fwd, rev):
            check(eid, a, b, i)
        for node in sorted(d.retyped_nodes):                 # a node newly admitted by step i
            b = p._node_index[node]
            for ei, a, _ in _back_hops(p, b, fwd, rev):
                check(p._edge_ids[ei], a, b, i)


# ---------------------------------------------------------------------------------------------- shortest path
def _shortest(ctx: SimpleNamespace) -> None:
    p, plan, answer, out, d = ctx.p, ctx.plan, ctx.answer, ctx.out, ctx.diff
    if answer["found"]:
        for hop in answer["witness"]["hops"]:
            if _gone(ctx, hop["to"]):
                out.append({"code": "node_gone", "node": hop["to"]})
            elif not _hop_ok(ctx, hop, plan.fwd, plan.rev):
                out.append({"code": "witness_gone", "edge": hop["edge"]})
    cert = answer["certificate"]
    if cert is None:
        out.append({"code": "no_certificate"})
        return
    ids = p._node_ids
    if cert["kind"] == "closure":
        reached = _lookup(cert["reached"], lambda x: x, d is not None)

        def check(eid: str, ei: int, a: int, b: int) -> None:
            if reached(ids[a]) is not None and reached(ids[b]) is None:
                out.append({"code": "certificate_broken", "edge": eid, "from": ids[a], "to": ids[b]})
        listed = cert["reached"]
    else:
        number = Fraction if plan.rational else (lambda x: x)
        bound = number(cert["bound"])
        entry = _lookup(cert["potentials"], lambda e: e["node"], d is not None)

        def value(n: str) -> Any:
            e = entry(n)
            return bound if e is None else number(e["value"])

        def check(eid: str, ei: int, a: int, b: int) -> None:
            if entry(ids[a]) is not None and value(ids[b]) > value(ids[a]) + plan.cost[ei]:
                out.append({"code": "certificate_broken", "edge": eid, "from": ids[a], "to": ids[b]})
        listed = None
    if d is not None:
        for eid, a, b in _added_hops(ctx, plan.fwd, plan.rev):
            check(eid, p._edge_index[eid], a, b)
        return
    for node in listed if listed is not None else (e["node"] for e in cert["potentials"]):
        u = p._node_index.get(node)
        if u is not None:
            for ei, v, _ in _hops(p, u, plan.fwd, plan.rev):
                check(p._edge_ids[ei], ei, u, v)


# ---------------------------------------------------------------------------------------------- bottleneck path
def _bottleneck(ctx: SimpleNamespace) -> None:
    p, plan, answer, out, d = ctx.p, ctx.plan, ctx.answer, ctx.out, ctx.diff
    if answer["found"]:
        for hop in answer["witness"]["hops"]:
            if _gone(ctx, hop["to"]):
                out.append({"code": "node_gone", "node": hop["to"]})
            elif not _hop_ok(ctx, hop, plan.fwd, plan.rev):
                out.append({"code": "witness_gone", "edge": hop["edge"]})
        if not answer["witness"]["hops"]:
            return                                       # a path with no edge: nothing can beat it
    cert = answer["certificate"]
    if cert is None:
        out.append({"code": "no_certificate"})
        return
    ids, threshold = p._node_ids, cert.get("threshold")
    reached = _lookup(cert["reached"], lambda x: x, d is not None)
    max_min = plan.objective == "max_min"

    def check(eid: str, ei: int, a: int, b: int) -> None:
        w = plan.weight[ei]
        if (threshold is None or (w > threshold if max_min else w < threshold)) \
                and reached(ids[a]) is not None and reached(ids[b]) is None:
            out.append({"code": "certificate_broken", "edge": eid, "from": ids[a], "to": ids[b]})

    if d is not None:
        for eid, a, b in _added_hops(ctx, plan.fwd, plan.rev):
            check(eid, p._edge_index[eid], a, b)
        return
    for node in cert["reached"]:
        u = p._node_index.get(node)
        if u is not None:
            for ei, v, _ in _hops(p, u, plan.fwd, plan.rev):
                check(p._edge_ids[ei], ei, u, v)
