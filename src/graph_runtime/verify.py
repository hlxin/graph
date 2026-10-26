"""Check a result against the input records, independently of the engine that produced it.

:func:`verify_result` reads the records a projection was built from (plain lookups, no integer remapping, nothing
from the engine). It checks the result's contract invariants (:func:`graph_runtime.result.contract_problems`) and
every witness in its answer: each hop names an input edge, with that edge's relation, in the requested scope and
direction, and the hops chain from a requested source to the node (or target) they witness; a shortest path's cost
is the exact sum of its hops' costs.

It also checks what makes an answer complete, without a second search. A complete reachability or impact answer
must be closed: every hop in scope from a node it expanded leads to a node it lists, no deeper than one more hop. A
certificate (requested with ``certificate: true``) must prove its typed or shortest path answer: closed states with
no last hop to a target, a closure with no target, or potentials that no hop in scope lowers by more than its cost,
0 at every source and at least the bound at every target. A support answer is recomputed from the records (by
re-evaluating each node's rule until nothing changes, not by the engine's counting), and every listed node, count
and cause is compared with it. Patterns and uncertified negatives are left to the tests' independent oracles. A
malformed result is reported as a problem, never raised.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from fractions import Fraction
from typing import Any

from .result import COMPLETED, INTERRUPTED, Result, contract_problems


def verify_result(result: Result | Mapping, *, nodes: Iterable[Any], edges: Iterable[Mapping],
                  type_parents: Mapping[str, Iterable[str]] | None = None,
                  relation_parents: Mapping[str, Iterable[str]] | None = None,
                  dimensions: int | None = None) -> list[str]:
    """Problems found in ``result`` (empty when it checks out). ``nodes``, ``edges`` and the hierarchies: the records
    the projection was built from, as :meth:`graph_runtime.Projection.build` takes them. A hop is in scope when its
    edge's relation, or a relation above it, is in the scope with the hop's direction; a node has a type when it
    declares it or a type below it.

    Records whose edges carry a ``shift`` (or ``dimensions`` given) are a periodic projection's cell: the result is
    then checked against the infinite graph they stand for, image by image (:mod:`graph_runtime.periodic`)."""
    try:
        data = dict(result._data) if isinstance(result, Result) else dict(result)      # read, never changed
        problems = contract_problems(data)
        if problems or data["status"] not in (COMPLETED, INTERRUPTED):
            return problems
        answer, request = data.get("answer"), data.get("request")
        if not isinstance(request, Mapping):
            return ["a completed or interrupted result carries its normalized request"]
        type_up = _ancestors(type_parents)
        edges = list(edges)
        if dimensions is not None or any("shift" in e for e in edges):
            from .periodic import PeriodicRecords, _ImageTypes, _LiftedEdges
            records = PeriodicRecords(nodes, edges, dimensions)
            check = _PeriodicChecker(problems, _LiftedEdges(records), _ImageTypes(records, type_up),
                                     _ancestors(relation_parents), records)
        else:
            types_of: dict[str, set[str]] = {}
            for n in nodes:
                declared = set() if isinstance(n, str) else set(n.get("types") or ())
                types_of[n if isinstance(n, str) else n["id"]] = declared.union(
                    *(type_up.get(t, ()) for t in declared))
            check = _Checker(problems, {e["id"]: (e["source"], e["target"], e["relation"]) for e in edges},
                             types_of, _ancestors(relation_parents))
        op, status = data.get("operation"), data["status"]
        if op in ("reachability", "dependency_impact"):
            check.node_set(op, request, answer, status)
        elif op == "typed_path":
            check.typed(request, answer)
        elif op == "shortest_path":
            check.shortest(request, answer, status)
        elif op == "bottleneck_path":
            check.bottleneck(request, answer, status)
        elif op == "pattern":
            check.pattern(request, answer)
        elif op == "support":
            check.support(request, answer, status, edges)
        elif op == "regular_path":
            check.regular(request, answer, status)
        elif op == "derivation":
            check.derivation(request, answer)
        elif op == "periodicity":
            check.periodicity(request, answer)
        elif op == "personalized_pagerank":
            check.pagerank(request, answer)
        else:
            problems.append(f"unknown operation {op!r}")
        return problems
    except Exception as exc:  # noqa: BLE001 - a malformed result is a problem to report, not an error to raise
        return [f"malformed result ({type(exc).__name__}: {exc})"]


def _ancestors(parents: Mapping[str, Iterable[str]] | None) -> dict[str, set[str]]:
    """Every name's ancestors in a hierarchy, by plain repeated expansion (independent of the projection's code)."""
    up = {k: set(v) for k, v in (parents or {}).items()}
    out: dict[str, set[str]] = {}
    for name in up:
        seen, todo = set(), list(up[name])
        while todo:
            x = todo.pop()
            if x not in seen:
                seen.add(x)
                todo.extend(up.get(x, ()))
        out[name] = seen
    return out


def _path_automaton(expr: Mapping) -> tuple[list, dict, set, bool]:
    """(steps, follow, accepting, nullable) of a normalized path expression, computed independently of
    :mod:`graph_runtime.paths`: every repetition is first written out as a tree of copies (``e{m,n}``: m copies, then
    n - m optional copies; ``e{m,}``: m - 1 copies, then one or more; ``e{0,}``: zero or more), the steps are numbered
    left to right from 1, and nullable, first, last and follow are computed on that tree by their textbook
    definitions. ``steps[i]`` is step i's expression (``steps[0]`` is None, the start)."""
    def expand(x: Mapping) -> tuple:
        op = x["op"]
        if op == "step":
            return ("step", x)
        if op in ("seq", "alt"):
            return (op, [expand(i) for i in x["items"]])
        lo, hi, item = x["min"], x["max"], x["item"]
        if hi is None:
            if lo == 0:
                return ("star", expand(item))
            return ("seq", [expand(item) for _ in range(lo - 1)] + [("plus", expand(item))])
        return ("seq", [expand(item) for _ in range(lo)] + [("opt", expand(item)) for _ in range(hi - lo)])

    steps: list = [None]

    def number(t: tuple) -> tuple:
        if t[0] == "step":
            steps.append(t[1])
            return ("step", len(steps) - 1)
        if t[0] in ("seq", "alt"):
            return (t[0], [number(c) for c in t[1]])
        return (t[0], number(t[1]))

    tree = number(expand(expr))

    def nullable(t: tuple) -> bool:
        kind = t[0]
        if kind == "step":
            return False
        if kind == "seq":
            return all(nullable(c) for c in t[1])
        if kind == "alt":
            return any(nullable(c) for c in t[1])
        return kind in ("star", "opt") or nullable(t[1])

    def first(t: tuple) -> set:
        kind = t[0]
        if kind == "step":
            return {t[1]}
        if kind == "alt":
            return set().union(*(first(c) for c in t[1]))
        if kind == "seq":
            out: set = set()
            for c in t[1]:
                out |= first(c)
                if not nullable(c):
                    break
            return out
        return first(t[1])

    def last(t: tuple) -> set:
        kind = t[0]
        if kind == "step":
            return {t[1]}
        if kind == "alt":
            return set().union(*(last(c) for c in t[1]))
        if kind == "seq":
            out: set = set()
            for c in reversed(t[1]):
                out |= last(c)
                if not nullable(c):
                    break
            return out
        return last(t[1])

    follow: dict[int, set] = {i: set() for i in range(len(steps))}

    def links(t: tuple) -> None:
        kind = t[0]
        if kind == "step":
            return
        if kind in ("seq", "alt"):
            for c in t[1]:
                links(c)
            if kind == "seq":
                for i, a in enumerate(t[1]):
                    for b in t[1][i + 1:]:
                        for x in last(a):
                            follow[x] |= first(b)
                        if not nullable(b):
                            break
            return
        links(t[1])
        if kind in ("star", "plus"):
            for x in last(t[1]):
                follow[x] |= first(t[1])

    links(tree)
    follow[0] = first(tree)
    empty = nullable(tree)
    return steps, follow, last(tree) | ({0} if empty else set()), empty


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _nearest(x: Fraction) -> float | None:
    try:
        return float(x)
    except OverflowError:
        return None


class _Checker:
    def __init__(self, problems: list[str], edge_of: Mapping, types_of: Mapping, relation_up: dict):
        self.problems, self.edge_of, self.types_of, self.relation_up = problems, edge_of, types_of, relation_up
        self._ends: tuple[dict, dict] | None = None

    def bad(self, message: str) -> None:
        self.problems.append(message)

    def in_scope(self, rel: str, direction: str, allowed: set) -> bool:
        return (rel, direction) in allowed or any((up, direction) in allowed for up in self.relation_up.get(rel, ()))

    def hops_from(self, u: Any, allowed: set) -> list[tuple[str, Any, str]]:
        """(edge, the node it leads to, its relation) for every hop in scope from ``u``, read from the records."""
        if self._ends is None:
            out: dict = {}
            into: dict = {}
            for eid, (src, dst, _) in self.edge_of.items():
                out.setdefault(src, []).append(eid)
                into.setdefault(dst, []).append(eid)
            self._ends = (out, into)
        out, into = self._ends
        hops = []
        for eid in out.get(u, ()):
            src, dst, rel = self.edge_of[eid]
            if self.in_scope(rel, "forward", allowed):
                hops.append((eid, dst, rel))
        for eid in into.get(u, ()):
            src, dst, rel = self.edge_of[eid]
            if self.in_scope(rel, "reverse", allowed):
                hops.append((eid, src, rel))
        return hops

    def hop(self, where: str, frm: Any, to: Any, edge: Any, relation: Any, direction: Any, allowed: set) -> None:
        """One hop: an input edge with this relation, in scope, followed in its direction from ``frm`` to ``to``."""
        rec = self.edge_of.get(edge)
        if rec is None:
            self.bad(f"{where}: edge {edge!r} is not an input record")
            return
        src, dst, rel = rec
        if relation != rel:
            self.bad(f"{where}: edge {edge} has the relation {rel}, not {relation}")
        if (rel, direction) not in allowed and not any((up, direction) in allowed
                                                       for up in self.relation_up.get(rel, ())):
            self.bad(f"{where}: {rel} {direction} is outside the requested scope")
        if direction == "forward":
            ends = (src, dst)
        elif direction == "reverse":
            ends = (dst, src)
        else:
            self.bad(f"{where}: direction {direction!r} is neither forward nor reverse")
            return
        if ends != (frm, to):
            self.bad(f"{where}: edge {edge} runs {src} -> {dst}; a {direction} hop from {frm} to {to} does not "
                     "follow it")

    def chain(self, where: str, witness: Any, sources: set, targets: Any, scopes: list[set],
              node_types: list | None = None) -> list[Mapping]:
        if not isinstance(witness, Mapping) or not isinstance(witness.get("hops"), list):
            self.bad(f"{where}: a found answer gives its witness, with its hops")
            return []
        hops = witness["hops"]
        if witness.get("length") != len(hops) or not _is_int(witness.get("length")):
            self.bad(f"{where}: length {witness.get('length')!r} but {len(hops)} hops")
        if witness.get("source") not in sources:
            self.bad(f"{where}: the witness starts at {witness.get('source')!r}, not at a requested source")
        if targets is not None and witness.get("target") not in targets:
            self.bad(f"{where}: the witness ends at {witness.get('target')!r}, not at a requested target")
        if witness.get("source") not in self.types_of or witness.get("target") not in self.types_of:
            self.bad(f"{where}: witness endpoints must be input nodes, including a zero-hop witness")
        if len(hops) > len(scopes) and len(scopes) > 1:
            self.bad(f"{where}: {len(hops)} hops for {len(scopes)} positions")
            return hops
        at = witness.get("source")
        for i, h in enumerate(hops):
            if not isinstance(h, Mapping):
                self.bad(f"{where}: hop {i} is not a hop")
                return hops
            if h.get("from") != at:
                self.bad(f"{where}: hop {i} starts at {h.get('from')!r}, not where the previous one ended ({at!r})")
            self.hop(f"{where}: hop {i}", h.get("from"), h.get("to"), h.get("edge"), h.get("relation"),
                     h.get("direction"), scopes[min(i, len(scopes) - 1)])
            wanted = node_types[i] if node_types is not None and i < len(node_types) else None
            if wanted is not None and not self.types_of.get(h.get("to"), set()) & set(wanted):
                self.bad(f"{where}: hop {i} lands on {h.get('to')!r}, which has none of the types {wanted}")
            at = h.get("to")
        if at != witness.get("target"):
            self.bad(f"{where}: the hops end at {at!r}, not at the target {witness.get('target')!r}")
        return hops

    def node_set(self, op: str, request: Mapping, answer: Mapping, status: str) -> None:
        strict = op == "dependency_impact"
        key = "affected" if strict else "reached"
        seeds = set(request["changed" if strict else "sources"])
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        max_depth = request.get("max_depth")
        entries = answer.get(key)
        if not isinstance(entries, list):
            self.bad(f"the answer lists {key}")
            return
        if answer.get("count") != len(entries) or not _is_int(answer.get("count")):
            self.bad(f"count {answer.get('count')!r} but {len(entries)} {key} entries")
        if len(entries) > request["limits"]["max_results"]:
            self.bad(f"{len(entries)} entries exceed max_results {request['limits']['max_results']}")
        if strict and sorted(answer.get("changed") or []) != sorted(seeds):
            self.bad("changed must list the requested changed nodes")
        by_node: dict[str, Mapping] = {}
        for e in entries:
            if e["node"] in by_node:
                self.bad(f"{e['node']!r} is listed twice")
            by_node[e["node"]] = e
            if e["node"] not in self.types_of:
                self.bad(f"{e['node']!r} is not an input node")
        # Each entry's own hop is checked once; its witness is then checked hop by hop back to a source, stopping
        # at an entry whose witness was already found sound from the same depth: every hop is checked once.
        sound: set[tuple[str, int]] = set()
        for e in entries:
            d, where = e["depth"], f"{key} {e['node']!r}"
            if not _is_int(d) or d < 0:
                self.bad(f"{where}: depth {d!r} is not a non-negative integer")
                continue
            if max_depth is not None and d > max_depth:
                self.bad(f"{where}: depth {d} exceeds max_depth {max_depth}")
            if d == 0:
                if strict:
                    self.bad(f"{where}: dependency impact lists nodes reached by at least one hop")
                elif e["node"] not in seeds or e["via"] is not None:
                    self.bad(f"{where}: depth 0 is a source, with no via")
                continue
            node, cur, walked = e["node"], e, []
            for step in range(d):
                if (node, d - step) in sound:
                    break
                if cur is None or not isinstance(cur.get("via"), Mapping):
                    self.bad(f"{where}: the witness breaks off after {step} of {d} hops")
                    walked = []
                    break
                via = cur["via"]
                before = len(self.problems)
                self.hop(f"{where}: hop {d - step} of {d}", via["from"], node, via["edge"], via["relation"],
                         via["direction"], allowed)
                if len(self.problems) == before:
                    walked.append((node, d - step))
                else:
                    walked = []
                node = via["from"]
                cur = by_node.get(node)
            else:
                if node not in seeds:
                    self.bad(f"{where}: the witness starts at {node!r}, which is not a requested source")
                    walked = []
            sound.update(walked)
        if not strict and status == COMPLETED:
            for s in sorted(seeds):
                if by_node.get(s, {}).get("depth") != 0:
                    self.bad(f"a complete reachability answer lists the source {s!r} at depth 0")
        if status != COMPLETED:
            return
        # closure: from every node it expanded (a changed node at depth 0, even when a cycle lists it deeper), every
        # hop in scope leads to a listed node at most one hop deeper; with the witnesses, the depths are then exact
        expanded = {s: 0 for s in seeds}
        for e in entries:
            expanded.setdefault(e["node"], e["depth"])
        for u, d in sorted(expanded.items(), key=lambda x: str(x[0])):
            if max_depth is not None and d >= max_depth:
                continue
            for eid, v, _ in self.hops_from(u, allowed):
                dv = by_node.get(v, {}).get("depth")
                if dv is None or dv > d + 1:
                    self.bad(f"incomplete: {v!r} is one hop ({eid}) from {u!r} at depth {d}, but "
                             + ("is not listed" if dv is None else f"is listed at depth {dv}"))

    def typed(self, request: Mapping, answer: Mapping) -> None:
        if answer.get("found") is not True:
            if answer.get("witness") is not None:
                self.bad("only a found witness is given")
            if answer.get("certificate") is not None:
                self.closed_states(request, answer["certificate"])
            return
        pattern = request["pattern"]
        scopes = [{(e["relation"], e["direction"]) for e in pos["scope"]} for pos in pattern]
        targets = None if request.get("targets") is None else set(request["targets"])
        hops = self.chain("witness", answer.get("witness"), set(request["sources"]), targets, scopes,
                          [pos.get("node_types") for pos in pattern])
        if len(hops) != len(pattern):
            self.bad(f"the witness has {len(hops)} hops for a pattern of {len(pattern)} positions")
        for i, h in enumerate(hops):
            if isinstance(h, Mapping) and (h.get("position") != i or not _is_int(h.get("position"))):
                self.bad(f"hop {i} says position {h.get('position')!r}")

    def closed_states(self, request: Mapping, cert: Mapping) -> None:
        """The states prove there is no witness: every source is a state at position 0, and every hop the pattern
        allows from a state leads to a state at the next position, or, from the last position, to a node that is not
        a target."""
        pattern, last = request["pattern"], len(request["pattern"])
        targets = None if request.get("targets") is None else set(request["targets"])
        states = {(st["node"], st["position"]) for st in cert["states"]}
        for s in request["sources"]:
            if (s, 0) not in states:
                self.bad(f"certificate: the source {s!r} is not a state at position 0")
        # a state whose node the records do not hold has no hop to follow: it neither helps nor harms the proof (an
        # answer kept across an edit that removed the node still proves its negative)
        for u, i in sorted(states, key=lambda x: (x[1], str(x[0]))):
            allowed = {(e["relation"], e["direction"]) for e in pattern[i]["scope"]}
            wanted = pattern[i].get("node_types")
            for eid, v, _ in self.hops_from(u, allowed):
                if wanted is not None and not self.types_of.get(v, set()) & set(wanted):
                    continue
                if i + 1 < last and (v, i + 1) not in states:
                    self.bad(f"certificate: the hop {eid} leads from state ({u!r}, {i}) to ({v!r}, {i + 1}), which "
                             "it does not list")
                elif i + 1 == last and (targets is None or v in targets):
                    self.bad(f"certificate: the hop {eid} from state ({u!r}, {i}) completes a witness at {v!r}")

    def admits(self, spec: Mapping, node: Any) -> bool:
        """Whether a node satisfies a pattern variable's constraints (types through the hierarchy, explicit IDs)."""
        if node not in self.types_of:
            return False
        if spec.get("node_types") is not None and not self.types_of[node] & set(spec["node_types"]):
            return False
        return spec.get("nodes") is None or node in set(spec["nodes"])

    def pattern(self, request: Mapping, answer: Mapping) -> None:
        """Every row: the matched variable admitted by match, and every link an input edge in its step's scope,
        joining the step's two bound variables in a scope direction, its child admitted by the step."""
        match, steps = request["match"], request["steps"]
        for n, row in enumerate(answer.get("rows") or []):
            nodes, links = row["nodes"], row["links"]
            if not self.admits(match, nodes.get(match["var"])):
                self.bad(f"row {n}: {match['var']} = {nodes.get(match['var'])!r} is not a node the match admits")
            for j, s in enumerate(steps):
                link = links[j] if j < len(links) else None
                if link is None:
                    if not s.get("optional"):
                        self.bad(f"row {n}: required step {j} has no link")
                    continue
                closing = "to" in s                     # a step that closes a cycle: its link joins two variables
                parent, child = nodes.get(s["from"]), nodes.get(s["to" if closing else "var"])
                rec = self.edge_of.get(link)
                if rec is None:
                    self.bad(f"row {n}: step {j}'s link {link!r} is not an input edge")
                    continue
                src, dst, rel = rec
                directions = [d for d, ends in (("forward", (src, dst)), ("reverse", (dst, src))) if ends == (parent, child)]
                allowed = {(e["relation"], e["direction"]) for e in s["scope"]}
                if not any((rel, d) in allowed or any((up, d) in allowed for up in self.relation_up.get(rel, ()))
                           for d in directions):
                    self.bad(f"row {n}: step {j}'s link {link} ({src} -{rel}-> {dst}) does not join {parent!r} to "
                             f"{child!r} in its scope")
                if not closing and not self.admits(s, child):
                    self.bad(f"row {n}: {s['var']} = {child!r} is not a node step {j} admits")
                if s.get("edges") is not None and link not in set(s["edges"]):
                    self.bad(f"row {n}: step {j}'s link {link} is not one of its edges")

    def shortest_certificate(self, request: Mapping, cert: Mapping) -> None:
        """A closure: every source, every hop from what it lists leads to what it lists, and no target is listed.
        Potentials: no hop in scope from a listed node lowers the value it leads to by more than its cost (an unlisted
        node has the bound), every source has 0 and every target at least the bound."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        sources, targets = set(request["sources"]), set(request["targets"])
        if cert["kind"] == "closure":
            reached = set(cert["reached"])
            for u in sorted(reached, key=str):
                for eid, v, _ in self.hops_from(u, allowed):
                    if v not in reached:
                        self.bad(f"certificate: the hop {eid} leads from {u!r} to {v!r}, which it does not list")
            for t in sorted(targets & reached, key=str):
                self.bad(f"certificate: the target {t!r} is reached")
            for s in sorted(sources - reached, key=str):
                self.bad(f"certificate: the source {s!r} is not listed")
            return
        (kind, table), = request["costs"].items()
        rational = any(isinstance(x, float) for x in table.values())
        number = (lambda x: Fraction(x)) if rational else (lambda x: x)
        bound = number(cert["bound"])
        value = {e["node"]: number(e["value"]) for e in cert["potentials"]}
        for s in sorted(sources, key=str):
            if value.get(s, bound) != 0:
                self.bad(f"certificate: the source {s!r} has {value.get(s, bound)}, not 0")
        for t in sorted(targets, key=str):
            if value.get(t, bound) < bound:
                self.bad(f"certificate: the target {t!r} has {value[t]}, below the bound {bound}")
        for u in sorted(value, key=str):
            for eid, v, rel in self.hops_from(u, allowed):
                c = table.get(rel if kind == "by_relation" else eid)
                if c is None:
                    self.bad(f"certificate: the hop {eid} in scope has no cost in the request")
                    continue
                if value.get(v, bound) > value[u] + (Fraction(c) if rational else c):
                    self.bad(f"certificate: the hop {eid} from {u!r} ({value[u]}) costs {c} but leads to {v!r} at "
                             f"{value.get(v, bound)}")

    def bottleneck(self, request: Mapping, answer: Mapping, status: str) -> None:
        """The witness's hops, each weighing what the request gives its edge, and its value; the certificate: what it
        lists is closed under the hops strictly better than its threshold (all hops for a closure), with every
        source and no target."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        (kind, table), = request["weights"].items()
        max_min = request["objective"] == "max_min"
        if answer.get("found") is True:
            hops = self.chain("witness", answer.get("witness"), set(request["sources"]), set(request["targets"]),
                              [allowed])
            weights = []
            for i, h in enumerate(hops):
                rec = self.edge_of.get(h.get("edge")) if isinstance(h, Mapping) else None
                if rec is None:
                    continue
                want = table.get(rec[2] if kind == "by_relation" else h.get("edge"))
                if h.get("weight") != want:
                    self.bad(f"hop {i}: weight {h.get('weight')!r}, but the request gives {want!r}")
                weights.append(want)
            value = (min if max_min else max)(weights) if weights else None
            if answer.get("value") != value:
                self.bad(f"the hops' bottleneck is {value!r}, but the answer says {answer.get('value')!r}")
        cert = answer.get("certificate")
        if cert is None:
            return
        threshold = cert.get("threshold")
        reached = set(cert["reached"])
        for u in sorted(reached, key=str):
            for eid, v, rel in self.hops_from(u, allowed):
                w = table.get(rel if kind == "by_relation" else eid)
                if w is None:
                    self.bad(f"certificate: the hop {eid} in scope has no weight in the request")
                    continue
                strictly_better = threshold is None or (w > threshold if max_min else w < threshold)
                if strictly_better and v not in reached:
                    self.bad(f"certificate: the hop {eid} (weight {w}) leads from {u!r} to {v!r}, which it does not "
                             "list")
        for t in sorted(set(request["targets"]) & reached, key=str):
            self.bad(f"certificate: the target {t!r} is reached")
        for src in sorted(set(request["sources"]) - reached, key=str):
            self.bad(f"certificate: the source {src!r} is not listed")

    def support(self, request: Mapping, answer: Mapping, status: str, edges: list[Mapping]) -> None:
        """Recompute, from the records alone, what stands before and after the failure, and compare the answer with
        it: the lost and weakened nodes (a prefix of each when an output limit cut the lists), each node's supports,
        the supports that still stand, the hops to those that fell, and the failed nodes it rests on."""
        if answer.get("lost") is None:
            return
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        every = set(request["all_of"] or ())

        def in_scope(rel: str, direction: str) -> bool:
            return (rel, direction) in allowed or any((up, direction) in allowed for up in self.relation_up.get(rel, ()))

        supports: dict[Any, set] = {n: set() for n in self.types_of}
        for e in edges:
            if in_scope(e["relation"], "forward"):
                supports[e["source"]].add(e["target"])
            if in_scope(e["relation"], "reverse"):
                supports[e["target"]].add(e["source"])
        dependents: dict[Any, set] = {n: set() for n in self.types_of}
        for u, sup in supports.items():
            for v in sup:
                dependents[v].add(u)

        def holds(u: Any, stands: set) -> bool:
            sup = supports[u]
            if every and self.types_of[u] & every:
                return all(v in stands for v in sup)
            return not sup or any(v in stands for v in sup)

        def fixpoint(down: set) -> set:
            stands: set = set()
            todo = deque(n for n in self.types_of if not supports[n])
            while todo:
                u = todo.popleft()
                if u in stands or u in down or not holds(u, stands):
                    continue
                stands.add(u)
                todo.extend(dependents[u])
            return stands

        failed = set(request["failed"])
        before, after = fixpoint(set()), fixpoint(failed)
        fallen = before - after
        region, todo = set(failed), deque(failed)
        while todo:
            for u in dependents[todo.popleft()]:
                if u not in region:
                    region.add(u)
                    todo.append(u)
        want_lost = sorted(u for u in fallen if u not in failed)
        want_weak = sorted(u for u in after if supports[u] & fallen)

        def causes(u: Any) -> list:
            found, seen, todo = set(), {u}, [u]
            while todo:
                for v in supports[todo.pop()] & fallen:
                    if v in failed:
                        found.add(v)
                    elif v not in seen:
                        seen.add(v)
                        todo.append(v)
            return sorted(found)

        counts = {"lost": len(want_lost), "weakened": len(want_weak),
                  "ungrounded": sum(1 for u in region if u not in before and u not in failed)}
        if answer.get("counts") != counts:
            self.bad(f"counts {answer.get('counts')!r}, but the records give {counts}")
        allowed_dirs = {"forward", "reverse"}
        for kind, want, got in (("lost", want_lost, answer["lost"]), ("weakened", want_weak, answer["weakened"])):
            names = [e.get("node") for e in got]
            if names != want[:len(names)] or (status == COMPLETED and len(names) != len(want)):
                self.bad(f"{kind}: the answer lists {names[:10]}, but the records give {want[:10]}")
            for e in got:
                u, where = e.get("node"), f"{kind} {e.get('node')!r}"
                if u not in supports:
                    continue
                if e.get("supports") != len(supports[u]):
                    self.bad(f"{where}: {e.get('supports')!r} supports, but the records give {len(supports[u])}")
                standing = len(supports[u] & after)
                if e.get("standing") != standing:
                    self.bad(f"{where}: {e.get('standing')!r} standing supports, but the records give {standing}")
                hops = e.get("failing") or []
                if {h.get("to") for h in hops} != supports[u] & fallen or len(hops) != len(supports[u] & fallen):
                    self.bad(f"{where}: its failing supports are not the ones that fell")
                for i, h in enumerate(hops):
                    if h.get("from") != u or h.get("direction") not in allowed_dirs:
                        self.bad(f"{where}: failing hop {i} does not start at it")
                        continue
                    self.hop(f"{where}: failing hop {i}", u, h.get("to"), h.get("edge"), h.get("relation"),
                             h.get("direction"), allowed)
                if e.get("causes") != causes(u):
                    self.bad(f"{where}: causes {e.get('causes')!r}, but the records give {causes(u)}")

    def regular(self, request: Mapping, answer: Mapping, status: str) -> None:
        """A regular path answer against the records and the path's automaton (computed here, independently of the
        engine): every hop is an input edge in its step's scope landing on its step's types, each state follows the
        one before it from the start state, and a witness ends in an accepting state at a target. A certificate
        must be closed below its bound (every move from a listed state leads to a listed state at most one hop
        further) and list no state that ends a match at a target, except, for reached nodes, ones the answer lists
        no shorter."""
        steps, follow, accepting, _ = _path_automaton(request["path"])
        sources = set(request["sources"])
        targets = None if request.get("targets") is None else set(request["targets"])
        ends_at = (lambda v: True) if targets is None else (lambda v: v in targets)

        def scope(i: int) -> set:
            return {(e["relation"], e["direction"]) for e in steps[i]["scope"]}

        def lands(i: int, v: Any) -> bool:
            wanted = steps[i]["node_types"]
            return wanted is None or bool(self.types_of.get(v, set()) & set(wanted))

        def move(where: str, frm: Any, to: Any, hop: Mapping, q: Any, i: Any) -> None:
            if not _is_int(i) or not 0 < i < len(steps) or not _is_int(q) or i not in follow.get(q, ()):
                self.bad(f"{where}: state {i!r} does not follow state {q!r} in the path's automaton")
                return
            self.hop(where, frm, to, hop.get("edge"), hop.get("relation"), hop.get("direction"), scope(i))
            if not lands(i, to):
                self.bad(f"{where}: {to!r} has none of the types step {i} lands on")

        if request["answer"] == "witness":
            if answer.get("found") is True:
                w = answer.get("witness") or {}
                hops = w.get("hops") or []
                if w.get("source") not in sources or (targets is not None and w.get("target") not in targets):
                    self.bad("the witness must run from a requested source to a requested target")
                at, q = w.get("source"), 0
                for n, h in enumerate(hops):
                    if h.get("from") != at:
                        self.bad(f"hop {n} starts at {h.get('from')!r}, not where the previous one ended ({at!r})")
                    move(f"hop {n}", h.get("from"), h.get("to"), h, q, h.get("state"))
                    at, q = h.get("to"), h.get("state")
                if at != w.get("target") or q not in accepting:
                    self.bad(f"the witness must end at its target in an accepting state, not at {at!r} in state {q!r}")
            elif answer.get("witness") is not None:
                self.bad("only a found witness is given")
        else:
            tree = {(t["node"], t["state"]): t for t in answer.get("tree") or []}
            for (node, state), t in tree.items():
                via = t.get("via")
                if via is None:
                    if node not in sources or state != 0:
                        self.bad(f"tree ({node!r}, {state}): only a source at the start state has no hop")
                    continue
                move(f"tree ({node!r}, {state})", via.get("from"), node, via, via.get("from_state"), state)
            for e in answer.get("reached") or []:
                if e.get("state") not in accepting or not ends_at(e.get("node")):
                    self.bad(f"reached {e.get('node')!r}: state {e.get('state')!r} does not end a match there")
        cert = answer.get("certificate")
        if cert is None:
            return
        bound = cert.get("bound")
        listed = {(st["node"], st["state"]): st["length"] for st in cert["states"]}
        reached = {e["node"]: e["length"] for e in answer.get("reached") or []}
        for (u, q), n in sorted(listed.items(), key=lambda x: (x[0][1], str(x[0][0]))):
            if q in accepting and ends_at(u) and not (u in reached and reached[u] <= n):
                self.bad(f"certificate: the state ({u!r}, {q}) ends a match at {u!r}, which the answer does not give")
            if bound is not None and n + 1 >= bound:
                continue
            for i in sorted(follow.get(q, ())):
                for eid, v, _ in self.hops_from(u, scope(i)):
                    if lands(i, v) and listed.get((v, i), n + 2) > n + 1:
                        self.bad(f"certificate: the hop {eid} moves from ({u!r}, {q}) to ({v!r}, {i}), which it does "
                                 f"not list within {n + 1} hops")

    def derivation(self, request: Mapping, answer: Mapping) -> None:
        """A derivation against the records: each listed node's rule is its own (given: a ground; all: a node of an
        all_of type, using one hop to each of its distinct inputs; one: any other node, using one hop), and every use
        is a hop in scope. A certificate must satisfy, at every node of the targets' region (the targets and their
        inputs, transitively, stopping at grounds), the condition that makes its values lower bounds: no rule gives a
        node a value better than the one listed for it (the bound for an unlisted node; none at all when the bound is
        null). With the derivation's values as upper bounds, that proves them best, and an unlisted target with no
        bound underivable."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        every = set(request["all_of"] or ())
        grounds = None if request["grounds"] is None else set(request["grounds"])
        objective = request["objective"]
        kind, table = next(iter(request["costs"].items())) if request["costs"] is not None else (None, None)
        rational = objective == "min_sum" and table is not None and any(isinstance(x, float) for x in table.values())
        never = object()                                  # no derivation: worse than every value

        def cost(eid: str, rel: str) -> Any:
            x = 1 if table is None else table.get(rel if kind == "by_relation" else eid)
            return Fraction(x) if rational and x is not None else x

        def better(a: Any, b: Any) -> bool:
            if a is never or b is never:
                return a is not never and b is never
            if objective == "min_sum":
                return a < b
            if a is None or b is None:
                return a is None and b is not None
            return a > b if objective == "max_min" else a < b

        def combine(a: Any, b: Any) -> Any:              # a hop's cost with its input's value, or two inputs
            if a is never or b is never:
                return never
            if objective == "min_sum":
                return a + b
            if a is None or b is None:
                return b if a is None else a
            return max(a, b) if objective == "min_max" else min(a, b)

        hops: dict[Any, list] = {}

        def hops_of(u: Any) -> list:
            if u not in hops:
                hops[u] = self.hops_from(u, allowed)
            return hops[u]

        def is_all(u: Any) -> bool:
            return bool(every and self.types_of.get(u, set()) & every)

        def is_ground(u: Any) -> bool:
            return u in grounds if grounds is not None else not hops_of(u) and not is_all(u)

        for e in answer.get("derivation") or []:
            u, rule, uses = e.get("node"), e.get("rule"), e.get("uses") or []
            where = f"derivation {u!r}"
            if u not in self.types_of:
                self.bad(f"{where}: not an input node")
                continue
            if rule == "given" and not is_ground(u):
                self.bad(f"{where}: given, but it is not a ground")
            elif rule != "given" and is_ground(u):
                self.bad(f"{where}: a ground is given, not derived")
            elif rule == "all" and (not is_all(u) or {h.get("to") for h in uses} != {v for _, v, _ in hops_of(u)}):
                self.bad(f"{where}: an all_of node uses one hop to each of its inputs")
            elif rule == "one" and is_all(u):
                self.bad(f"{where}: a node of an all_of type needs every input, not one")
            for n, h in enumerate(uses):
                self.hop(f"{where}: use {n}", u, h.get("to"), h.get("edge"), h.get("relation"), h.get("direction"),
                         allowed)
        cert = answer.get("certificate")
        if cert is None:
            return

        def read(entry: Mapping, field: str) -> Any:
            return (None if entry.get(f"{field}_exact") is None else Fraction(entry[f"{field}_exact"])) \
                if rational else entry.get(field)

        identity = 0 if objective == "min_sum" else None
        value = {c["node"]: read(c, "value") for c in cert["values"]}
        bound = read(cert, "bound")
        rest = never if bound is None else bound

        def worth(u: Any) -> Any:
            return value[u] if u in value else rest

        region, todo = set(request["targets"]), list(request["targets"])
        while todo:
            u = todo.pop()
            if is_ground(u):
                continue
            for _, v, _ in hops_of(u):
                if v not in region:
                    region.add(v)
                    todo.append(v)
        for u in sorted(region, key=str):
            claim = worth(u)
            uncosted = [eid for eid, _, rel in ([] if is_ground(u) else hops_of(u)) if cost(eid, rel) is None]
            if uncosted:
                self.bad(f"certificate: the hop {uncosted[0]} in scope has no cost in the request")
                continue
            if is_ground(u):
                offers = [identity]
            elif is_all(u):
                best: dict[Any, Any] = {}                 # each input through its best hop
                for eid, v, rel in hops_of(u):
                    x = combine(cost(eid, rel), worth(v))
                    if v not in best or better(x, best[v]):
                        best[v] = x
                total = identity
                for x in best.values():
                    total = combine(total, x)
                offers = [total]
            else:
                offers = [combine(cost(eid, rel), worth(v)) for eid, v, rel in hops_of(u)]
            for x in offers:
                if better(x, claim):
                    self.bad(f"certificate: a rule gives {u!r} a value better than the "
                             f"{'listed' if u in value else 'bound'} {claim if claim is not never else 'none'}")
                    break

    def pagerank(self, request: Mapping, answer: Mapping) -> None:
        """A ranking against the records, from its certificate (the estimates p): every residual is recomputed
        exactly from the PageRank equation, r = s - p (I - (1 - alpha) P) / alpha, and must not be negative; then
        pi = p + sum_u r(u) pi_u bounds every node's PageRank, p(v) <= pi(v) <= p(v) + r(v) + (1 - alpha) (R - r(v)),
        and the listed intervals, the residual and the certified prefix must be exactly those. Without a
        certificate only the records' nodes are checked."""
        for e in answer.get("ranked") or []:
            if e.get("node") not in self.types_of:
                self.bad(f"ranked {e.get('node')!r} is not an input node")
        cert = answer.get("certificate")
        if cert is None:
            return
        alpha = Fraction(request["alpha"])
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        estimate = {e["node"]: Fraction(e["value_exact"]) for e in cert["values"]}
        sources = request["sources"]
        residual: dict[Any, Fraction] = {s: Fraction(1, len(sources)) for s in sources}
        for u, x in estimate.items():
            if u not in self.types_of:
                self.bad(f"certificate: {u!r} is not an input node")
                continue
            residual[u] = residual.get(u, Fraction(0)) - x / alpha
            hops = self.hops_from(u, allowed)
            for _, v, _ in hops:
                residual[v] = residual.get(v, Fraction(0)) + (1 - alpha) * x / (alpha * len(hops))
        negative = sorted((u for u, r in residual.items() if r < 0), key=str)
        if negative:
            self.bad(f"certificate: the estimates leave a negative residual at {negative[:5]}: they overstate the "
                     "PageRank")
            return
        total = sum(residual.values(), Fraction(0))
        if Fraction(answer["residual_exact"]) != total:
            self.bad(f"the residual is {total}, not {answer['residual_exact']}")

        def upper(u: Any) -> Fraction:
            r = residual.get(u, Fraction(0))
            return estimate.get(u, Fraction(0)) + r + (1 - alpha) * (total - r)
        listed = []
        for e in answer.get("ranked") or []:
            u = e["node"]
            lo, hi = Fraction(e["lower_exact"]), Fraction(e["upper_exact"])
            if lo != estimate.get(u, Fraction(0)) or hi != upper(u):
                self.bad(f"ranked {u!r}: the records prove [{estimate.get(u, 0)}, {upper(u)}], not [{lo}, {hi}]")
            listed.append(u)
        rest = [upper(u) for u in set(estimate) | set(residual) if u not in listed] + [(1 - alpha) * total]
        m = answer.get("certified_top", 0)
        if m and min(estimate.get(u, Fraction(0)) for u in listed[:m]) <= max(
                rest + [upper(u) for u in listed[m:]]):
            self.bad(f"the first {m} ranked nodes are not proven the top ones")

    def shortest(self, request: Mapping, answer: Mapping, status: str) -> None:
        if answer.get("certificate") is not None:
            self.shortest_certificate(request, answer["certificate"])
        if answer.get("found") is not True:
            if answer.get("witness") is not None:
                self.bad("only a found witness is given")
            return
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        hops = self.chain("witness", answer.get("witness"), set(request["sources"]), set(request["targets"]),
                          [allowed])
        (kind, table), = request["costs"].items()
        rational = any(isinstance(x, float) for x in table.values())
        total: Any = Fraction(0) if rational else 0
        for i, h in enumerate(hops):
            rec = self.edge_of.get(h.get("edge")) if isinstance(h, Mapping) else None
            if rec is None:
                continue
            want = table.get(rec[2] if kind == "by_relation" else h.get("edge"))
            if h.get("cost") != want or type(h.get("cost")) is not type(want):
                self.bad(f"hop {i}: cost {h.get('cost')!r}, but the request gives {want!r}")
            if want is not None:
                total += Fraction(want) if rational else want
        if rational:
            reported = Fraction(answer["cost_exact"])
            if answer.get("cost") != _nearest(reported):
                self.bad(f"cost {answer.get('cost')!r} is not the nearest float to cost_exact {answer['cost_exact']}")
        else:
            reported = answer.get("cost")
            if not _is_int(reported):
                self.bad(f"an integer-cost answer gives an integer cost, not {reported!r}")
        if total != reported:
            self.bad(f"the hops cost {total}, but the answer says {reported!r}")
        if status == INTERRUPTED:
            low = answer.get("lower_bound_exact") if rational else answer.get("lower_bound")
            low = Fraction(low) if rational and low is not None else low
            if low is None or low > reported:
                self.bad("an unproven witness comes with a lower bound no greater than its cost")


class _PeriodicChecker(_Checker):
    """The checker over a periodic projection's infinite graph: edges and types are looked up image by image, and an
    image's hops are enumerated from the cell's records (:class:`graph_runtime.periodic.PeriodicRecords`)."""

    def __init__(self, problems: list[str], edge_of: Mapping, types_of: Mapping, relation_up: dict, records: Any):
        super().__init__(problems, edge_of, types_of, relation_up)
        self.records = records

    def hops_from(self, u: Any, allowed: set) -> list[tuple[str, Any, str]]:
        return [(eid, v, rel) for eid, v, rel, direction in self.records.hops(u)
                if self.in_scope(rel, direction, allowed)]

    def periodicity(self, request: Mapping, answer: Mapping) -> None:
        """Each component against the cell's records: its tree's hops are edges in scope between the images it names;
        every hop in scope from a listed node's image leads to a listed node, by a lattice vector from that node's
        listed image (so nothing outside the listed nodes belongs, and every cycle's net lies in the lattice); and the
        cycles are exactly the edges among the listed nodes whose net is not zero, with the nets the records give.
        With the witnesses, which write each basis row as a combination of these nets, the basis generates exactly the
        component's translation lattice."""
        from .lattice import coefficients
        from .periodic import image_id
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        for n, c in enumerate(answer.get("components") or []):
            where = f"component {n}"
            images = {x["node"]: tuple(x["image"]) for x in c["nodes"]}
            basis = [tuple(r) for r in c["basis"]]
            for x in c["nodes"]:
                via = x["via"]
                if via is not None:
                    self.hop(f"{where}: the tree hop to {x['node']!r}", via["from"], image_id(x["node"], x["image"]),
                             via["edge"], via["relation"], via["direction"], allowed)
            nets = {}
            for node, img in sorted(images.items()):
                for eid, v, _ in self.hops_from(image_id(node, img), allowed):
                    base, _, text = v.rpartition("@")
                    if base not in images:
                        self.bad(f"{where}: the hop {eid} leads from {node!r} to {base!r}, which it does not list")
                        continue
                    offset = [int(t) - q for t, q in zip(text.split(","), images[base])]
                    if coefficients(basis, offset) is None:
                        self.bad(f"{where}: the hop {eid} closes a cycle whose net {offset} is not in the lattice")
                    edge = eid.rpartition("@")[0]
                    if eid == image_id(edge, img) and self.records.edges[edge][0] == node and any(offset):
                        nets[edge] = tuple(offset)                # the edge from its source: its cycle's net
            listed = {cyc["edge"]: tuple(cyc["net"]) for cyc in c["cycles"]}
            if listed != nets:
                self.bad(f"{where}: its cycles are not the edges among its nodes with a nonzero net "
                         f"({sorted(set(listed) ^ set(nets))[:5]} differ)")
