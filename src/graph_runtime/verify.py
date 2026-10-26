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
                  relation_parents: Mapping[str, Iterable[str]] | None = None) -> list[str]:
    """Problems found in ``result`` (empty when it checks out). ``nodes``, ``edges`` and the hierarchies: the records
    the projection was built from, as :meth:`graph_runtime.Projection.build` takes them. A hop is in scope when its
    edge's relation, or a relation above it, is in the scope with the hop's direction; a node has a type when it
    declares it or a type below it."""
    try:
        data = dict(result._data) if isinstance(result, Result) else dict(result)      # read, never changed
        problems = contract_problems(data)
        if problems or data["status"] not in (COMPLETED, INTERRUPTED):
            return problems
        answer, request = data.get("answer"), data.get("request")
        if not isinstance(request, Mapping):
            return ["a completed or interrupted result carries its normalized request"]
        type_up = _ancestors(type_parents)
        types_of: dict[str, set[str]] = {}
        for n in nodes:
            declared = set() if isinstance(n, str) else set(n.get("types") or ())
            types_of[n if isinstance(n, str) else n["id"]] = declared.union(*(type_up.get(t, ()) for t in declared))
        edges = list(edges)
        check = _Checker(problems, {e["id"]: (e["source"], e["target"], e["relation"]) for e in edges}, types_of,
                         _ancestors(relation_parents))
        op, status = data.get("operation"), data["status"]
        if op in ("reachability", "dependency_impact"):
            check.node_set(op, request, answer, status)
        elif op == "typed_path":
            check.typed(request, answer)
        elif op == "shortest_path":
            check.shortest(request, answer, status)
        elif op == "pattern":
            check.pattern(request, answer)
        elif op == "support":
            check.support(request, answer, status, edges)
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


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _nearest(x: Fraction) -> float | None:
    try:
        return float(x)
    except OverflowError:
        return None


class _Checker:
    def __init__(self, problems: list[str], edge_of: dict, types_of: dict, relation_up: dict):
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
                parent, child = nodes.get(s["from"]), nodes.get(s["var"])
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
                if not self.admits(s, child):
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
