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
  certificates (pattern), a negative or an optimum without its certificate, and a certificate an edit
  broke without refuting the answer.

Components, biconnectivity, maximum flows, matchings, dominators, incidence, critical paths, d-separation and support
are revalidated by their certificates: those prove the whole answer from the records alone (trees and an ordered
closed partition, a depth-first forest, a cut, a vertex cover, a low-high order, a nonsingular minor with a kernel
basis, a topological order with earliest times, Bayes-ball states, both least fixpoints' ranks), so the answer holds
on the new projection exactly when its certificate still checks
against the new projection's records (:func:`graph_runtime.verify_result`), and a listed node or edge that is gone
refutes it. These are always checked in full.

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
from .verify import _check

HOLDS, FAILS, UNKNOWN = "holds", "fails", "unknown"
VERDICTS = (HOLDS, FAILS, UNKNOWN)
REVALIDATION_KIND = "graph_runtime.revalidation"
MAX_REASONS = 100
_RECHECKED = ("components", "biconnectivity", "max_flow", "matching", "dominators",   # by verifying the certificate
              "incidence", "critical_path", "d_separation", "support", "spanning_forest", "cycle_basis",
              "min_cost_flow", "reachability_pairs")
_SELF_PROVEN = {"critical_path": ("acyclic", False), "d_separation": ("separated", False)}   # a witness is the proof
_PROVEN_BY_ANSWER = ("spanning_forest",)       # what the answer claims, checked against the records, is the proof
_NODE_FIELDS = frozenset({"node", "nodes", "left", "right", "idom", "source", "target", "from", "to", "dominators"})
_EDGE_FIELDS = frozenset({"edge", "edges"})
_REFUTING = {"node_gone", "witness_gone", "type_changed", "rule_changed", "reaches_unlisted", "reaches_shallower",
             "unanswerable", "answer_refuted"}


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
    use = d is not None and d.old == data.get("projection") and d.new == p.identity and not d.vocabulary_changed \
        and data.get("operation") not in ("derivation", "personalized_pagerank") + _RECHECKED  # in full
    checked = "diff" if use else "answer"
    if data.get("status") != COMPLETED:
        return [{"code": "not_complete", "status": data.get("status")}], checked
    if data.get("projection") == p.identity:
        return [], checked
    op = data["operation"]
    if op not in ("reachability", "dependency_impact", "typed_path", "shortest_path", "bottleneck_path",
                  "regular_path", "derivation", "personalized_pagerank") + _RECHECKED:
        return [{"code": "not_certified", "operation": op}], checked
    try:
        req = normalize_request({**data["request"], "projection": p.identity})
        plan = (_bind_costs_of_edit(p, req, d) if use and op in ("shortest_path", "bottleneck_path")
                else _bind(p, req))
    except RequestError as e:
        return [{"code": "unanswerable", "error": e.code}], checked
    ctx = SimpleNamespace(p=p, plan=plan, request=data["request"], answer=data["answer"], diff=d if use else None,
                          out=[], data=data)
    if op in ("reachability", "dependency_impact"):
        _sets(ctx, strict=op == "dependency_impact")
    elif op == "typed_path":
        _typed(ctx)
    elif op == "shortest_path":
        _shortest(ctx)
    elif op == "regular_path":
        _regular(ctx)
    elif op == "derivation":
        _derivation(ctx)
    elif op == "personalized_pagerank":
        _pagerank(ctx)
    elif op in _RECHECKED:
        _recheck(ctx)
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


# ---------------------------------------------------------------------------------------------- derivation
def _derivation(ctx: SimpleNamespace) -> None:
    """A derivation holds while every listed node keeps its rule (a ground stays a ground, an all_of node keeps its
    type and exactly the inputs it uses, a one-input node does not become all_of) and every use is still a hop in
    scope; its claims of best values and of underivable targets rest on its certificate, whose conditions are checked
    again over the targets' region in the new projection. A derivation is local to no part of the graph that a diff
    can bound (an edit far away can join the region), so it is always checked in full."""
    from .engine import _Objective
    p, plan, answer, out = ctx.p, ctx.plan, ctx.answer, ctx.out
    fwd, rev, every, grounds = plan.fwd, plan.rev, plan.all_of, plan.grounds
    obj, rational, mask = _Objective(plan.objective), plan.rational, p._node_mask
    cache: dict[int, list] = {}

    def hops(u: int) -> list:
        if u not in cache:
            cache[u] = list(_hops(p, u, fwd, rev))
        return cache[u]

    def is_all(u: int) -> bool:
        return every is not None and bool(mask[u] & every)

    def is_ground(u: int) -> bool:
        return u in grounds if grounds is not None else not hops(u) and not is_all(u)

    for e in answer["derivation"]:
        u = p._node_index.get(e["node"])
        if u is None:
            out.append({"code": "node_gone", "node": e["node"]})
            continue
        rule = e["rule"]
        if (rule == "given") != is_ground(u):
            out.append({"code": "rule_changed", "node": e["node"]})
        elif rule != "given" and (rule == "all") != is_all(u):
            out.append({"code": "type_changed", "node": e["node"]})
        elif rule == "all" and {h["to"] for h in e["uses"]} != {p._node_ids[v] for _, v, _ in hops(u)}:
            out.append({"code": "rule_changed", "node": e["node"]})
        for h in e["uses"]:
            if not _hop_ok(ctx, h, fwd, rev):
                out.append({"code": "witness_gone", "edge": h["edge"]})
    cert = answer["certificate"]
    if cert is None:
        out.append({"code": "no_certificate"})
        return
    never = object()

    def read(entry: Mapping, field: str) -> Any:
        if rational:
            exact = entry.get(f"{field}_exact")
            return None if exact is None else Fraction(exact)
        return entry.get(field)

    value = {p._node_index[c["node"]]: read(c, "value") for c in cert["values"] if c["node"] in p._node_index}
    bound = read(cert, "bound")
    rest = never if bound is None else bound

    def better(a: Any, b: Any) -> bool:
        if a is never or b is never:
            return a is not never and b is never
        return obj.better(a, b)

    def hop(ei: int, v: int) -> Any:
        x = value.get(v, rest)
        if x is never:
            return never
        c = plan.cost[ei] if plan.cost is not None else 1
        return obj.hop(c, x)

    region, todo = set(plan.targets), list(plan.targets)
    while todo:
        u = todo.pop()
        if is_ground(u):
            continue
        for _, v, _ in hops(u):
            if v not in region:
                region.add(v)
                todo.append(v)
    for u in sorted(region):
        claim = value.get(u, rest)
        if is_ground(u):
            offers = [obj.identity]
        elif is_all(u):
            best: dict[int, Any] = {}
            for ei, v, _ in hops(u):
                x = hop(ei, v)
                if v not in best or better(x, best[v]):
                    best[v] = x
            total = obj.identity
            for x in best.values():
                total = never if total is never or x is never else obj.fold(total, x)
            offers = [total]
        else:
            offers = [hop(ei, v) for ei, v, _ in hops(u)]
        if any(better(x, claim) for x in offers):
            out.append({"code": "certificate_broken", "node": p._node_ids[u]})


# ---------------------------------------------------------------------------------------------- by certificate
def _recheck(ctx: SimpleNamespace) -> None:
    """An answer its certificate proves from the records alone: every node and edge it names must still exist (one
    that is gone refutes it), what it claims must agree with the new projection's records (a contradiction, such as
    a kernel vector that no longer multiplies to zero or a flow beyond a capacity, refutes it), and its certificate
    must check against them, which then proves the answer correct there; one that no longer checks leaves it
    unknown."""
    p, answer, out = ctx.p, ctx.answer, ctx.out
    nodes: set = set()
    edges: set = set()
    _names({k: v for k, v in answer.items() if k != "certificate"}, None, nodes, edges)
    out.extend({"code": "node_gone", "node": n} for n in sorted(n for n in nodes if n not in p._node_index))
    out.extend({"code": "witness_gone", "edge": e} for e in sorted(e for e in edges if e not in p._edge_index))
    if out:
        return
    records = p._memo.get("records")
    if records is None:
        records = p._memo["records"] = p.to_dict()
    args = (ctx.data, records["nodes"], records["edges"], records["type_parents"], records["relation_parents"], None)
    claims = _check(*args, False)                 # what the answer says, without its proof: a contradiction refutes it
    if claims:
        out.append({"code": "answer_refuted", "problem": claims[0][:300]})
        return
    if ctx.data["operation"] == "reachability_pairs":
        unproven = not ctx.request["witnesses"] and any(x["reachable"] and x["source"] != x["target"]
                                                        for x in answer["pairs"])
        if unproven:                              # a positive needs its witness: closures prove only negatives
            out.append({"code": "no_certificate"})
            return
        if all(x["reachable"] for x in answer["pairs"]):
            return                                # every pair a checked witness (or a node reaching itself)
    field_value = _SELF_PROVEN.get(ctx.data["operation"])
    if field_value is not None and answer.get(field_value[0]) is field_value[1] \
            or ctx.data["operation"] in _PROVEN_BY_ANSWER:
        return                                    # a cycle, an active trail, a forest: checked above, they prove it
    if answer.get("certificate") is None:
        out.append({"code": "no_certificate"})
        return
    problems = _check(*args, True)
    if problems:
        out.append({"code": "certificate_broken", "problem": problems[0][:300]})


def _names(value: Any, field: Any, nodes: set, edges: set) -> None:
    """The node and edge IDs an answer names, by the fields that hold them."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            _names(v, k, nodes, edges)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _names(v, field, nodes, edges)
    elif isinstance(value, str):
        if field in _NODE_FIELDS:
            nodes.add(value)
        elif field in _EDGE_FIELDS:
            edges.add(value)


# ---------------------------------------------------------------------------------------------- pagerank
def _pagerank(ctx: SimpleNamespace) -> None:
    """A ranking holds while its proof does on the new projection, as ``verify_result`` checks it: its estimates,
    put into the PageRank equation of the new walk, leave no negative residual, and the residual, intervals and
    certified prefix they prove are exactly the ones the answer gives (tighter ones would leave its exact numbers
    false). Every residual depends on the walk's hops everywhere the estimates reach, so a ranking is always checked
    in full."""
    p, plan, answer, out = ctx.p, ctx.plan, ctx.answer, ctx.out
    for e in answer["ranked"]:
        if e["node"] not in p._node_index:
            out.append({"code": "node_gone", "node": e["node"]})
    cert = answer["certificate"]
    if cert is None:
        out.append({"code": "no_certificate"})
        return
    if out:
        return
    alpha = plan.alpha
    estimate = {p._node_index[e["node"]]: Fraction(e["value_exact"]) for e in cert["values"]
                if e["node"] in p._node_index}
    if len(estimate) != len(cert["values"]):
        out.append({"code": "certificate_broken", "node": next(e["node"] for e in cert["values"]
                                                                if e["node"] not in p._node_index)})
        return
    residual: dict[int, Fraction] = {s: Fraction(1, len(plan.sources)) for s in plan.sources}
    for u, x in estimate.items():
        residual[u] = residual.get(u, Fraction(0)) - x / alpha
        hops = list(_hops(p, u, plan.fwd, plan.rev))
        for _, v, _ in hops:
            residual[v] = residual.get(v, Fraction(0)) + (1 - alpha) * x / (alpha * len(hops))
    negative = [u for u, r in residual.items() if r < 0]
    if negative:
        out.append({"code": "certificate_broken", "node": p._node_ids[min(negative)]})
        return
    total = sum(residual.values(), Fraction(0))
    if total != Fraction(answer["residual_exact"]):
        out.append({"code": "certificate_broken",
                    "problem": f"the residual is {total}, not {answer['residual_exact']}"})
        return

    def upper(u: int) -> Fraction:
        r = residual.get(u, Fraction(0))
        return estimate.get(u, Fraction(0)) + r + (1 - alpha) * (total - r)
    listed = [p._node_index[e["node"]] for e in answer["ranked"]]
    for e, u in zip(answer["ranked"], listed):
        if Fraction(e["lower_exact"]) != estimate.get(u, Fraction(0)) or Fraction(e["upper_exact"]) != upper(u):
            out.append({"code": "certificate_broken", "node": e["node"]})
            return
    m = answer["certified_top"]
    rest = [upper(u) for u in set(estimate) | set(residual) if u not in listed] + [(1 - alpha) * total]
    if m and min(estimate.get(u, Fraction(0)) for u in listed[:m]) <= max(rest + [
            upper(u) for u in listed[m:]]):
        out.append({"code": "certificate_broken", "node": answer["ranked"][m - 1]["node"]})


# ---------------------------------------------------------------------------------------------- shortest path
def _shortest(ctx: SimpleNamespace) -> None:
    """The witness (or a negative cycle's three walks) must still be hops of the new projection; a negative cycle then
    still makes walks ever cheaper, whatever else changed. A certificate must still hold for every hop in scope from
    what it lists: a closure stays closed, potentials stay unlowered, feasible potentials stay feasible and their dead
    ends closed. With a diff, only the added hops need checking: removing records breaks none of these."""
    p, plan, answer, out, d = ctx.p, ctx.plan, ctx.answer, ctx.out, ctx.diff
    cycle = answer.get("negative_cycle")
    walks = ([cycle["lead_in"], cycle["cycle"], cycle["lead_out"]] if cycle is not None
             else [answer["witness"]] if answer["found"] else [])
    for walk in walks:
        for hop in walk["hops"]:
            if _gone(ctx, hop["to"]):
                out.append({"code": "node_gone", "node": hop["to"]})
            elif not _hop_ok(ctx, hop, plan.fwd, plan.rev):
                out.append({"code": "witness_gone", "edge": hop["edge"]})
    if cycle is not None:
        return
    cert = answer["certificate"]
    if cert is None:
        out.append({"code": "no_certificate"})
        return
    ids = p._node_ids
    number = Fraction if plan.rational else (lambda x: x)
    if cert["kind"] == "closure":
        reached = _lookup(cert["reached"], lambda x: x, d is not None)

        def check(eid: str, ei: int, a: int, b: int) -> None:
            if reached(ids[a]) is not None and reached(ids[b]) is None:
                out.append({"code": "certificate_broken", "edge": eid, "from": ids[a], "to": ids[b]})
    elif cert["kind"] == "feasible_potentials":
        entry = _lookup(cert["potentials"], lambda e: e["node"], d is not None)
        dead = _lookup(cert["dead_ends"], lambda x: x, d is not None)

        def check(eid: str, ei: int, a: int, b: int) -> None:
            if dead(ids[a]) is not None:
                ok = dead(ids[b]) is not None
            elif entry(ids[a]) is not None:
                to = entry(ids[b])
                ok = dead(ids[b]) is not None or to is not None and \
                    number(to["value"]) <= number(entry(ids[a])["value"]) + plan.cost[ei]
            else:
                ok = True
            if not ok:
                out.append({"code": "certificate_broken", "edge": eid, "from": ids[a], "to": ids[b]})
    else:
        bound = number(cert["bound"])
        entry = _lookup(cert["potentials"], lambda e: e["node"], d is not None)

        def value(n: str) -> Any:
            e = entry(n)
            return bound if e is None else number(e["value"])

        def check(eid: str, ei: int, a: int, b: int) -> None:
            if entry(ids[a]) is not None and value(ids[b]) > value(ids[a]) + plan.cost[ei]:
                out.append({"code": "certificate_broken", "edge": eid, "from": ids[a], "to": ids[b]})
    if d is not None:                         # only the added hops: the certificate is read where they start
        for eid, a, b in _added_hops(ctx, plan.fwd, plan.rev):
            check(eid, p._edge_index[eid], a, b)
        return
    listed = (cert["reached"] if cert["kind"] == "closure" else [e["node"] for e in cert["potentials"]]
              + (list(cert["dead_ends"]) if cert["kind"] == "feasible_potentials" else []))
    for node in listed:
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
