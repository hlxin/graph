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

import heapq
from collections import deque
from collections.abc import Iterable, Mapping
from fractions import Fraction
from math import gcd
from typing import Any

from .result import COMPLETED, INTERRUPTED, Result, _acyclic, contract_problems


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
    return _check(result, nodes, edges, type_parents, relation_parents, dimensions, True)


def _check(result: Result | Mapping, nodes: Iterable[Any], edges: Iterable[Mapping],
           type_parents: Mapping[str, Iterable[str]] | None, relation_parents: Mapping[str, Iterable[str]] | None,
           dimensions: int | None, proofs: bool) -> list[str]:
    """verify_result; with ``proofs`` false, only what the answer claims is checked, not its certificate (what
    revalidation tells apart: a claim the records contradict refutes the answer, a broken proof does not)."""
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
        check.proofs = proofs
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
        elif op == "components":
            check.components(request, answer, status)
        elif op == "biconnectivity":
            check.biconnectivity(request, answer, status)
        elif op == "max_flow":
            check.max_flow(request, answer, status)
        elif op == "matching":
            check.matching(request, answer, status)
        elif op == "dominators":
            check.dominators(request, answer, status)
        elif op == "incidence":
            check.incidence(request, answer, status)
        elif op == "critical_path":
            check.critical_path(request, answer, status)
        elif op == "d_separation":
            check.d_separation(request, answer, status)
        elif op == "spanning_forest":
            check.spanning_forest(request, answer, status)
        elif op == "cycle_basis":
            check.cycle_basis(request, answer, status)
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
        self.proofs = True                     # False: check what the answer claims, not its certificate

    def proof(self, answer: Mapping) -> Any:
        """The answer's certificate, when proofs are checked."""
        return answer.get("certificate") if self.proofs else None

    def bad(self, message: str) -> None:
        self.problems.append(message)

    def spanning_forest(self, request: Mapping, answer: Mapping, status: str) -> None:
        """The forest against the records, which prove it (Tarjan's cycle property): its edges are links in scope, no
        loop and no cycle among them; every other link in scope joins two nodes of one of its trees (so they span
        every piece) and comes after every edge of the tree path between its ends in the order of (weight, edge ID)
        (weight descending for a maximum), the path's last edge found by binary lifting; the count, the trees and
        the exact total weight are the forest's. Such a forest is the one Kruskal's algorithm keeps."""
        if status != COMPLETED:
            return
        if not isinstance(answer.get("edges"), (list, tuple)):      # a frozen answer holds tuples
            self.bad("a complete spanning forest lists its edges")
            return
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        weights = request["weights"]
        kind, table = next(iter(weights.items())) if weights else (None, None)
        rational = table is not None and any(isinstance(x, float) for x in table.values())
        sign = -1 if request["objective"] == "max" else 1

        def weight(eid: str, rel: str) -> Any:
            if table is None:
                return 1
            x = table.get(rel if kind == "by_relation" else eid)
            if x is None:
                self.bad(f"the link {eid} in scope has no weight in the request")
                return 0
            return Fraction(x) if rational else x

        links = {eid: (s, t, rel) for eid, (s, t, rel) in self.edge_of.items()
                 if s != t and (self.in_scope(rel, "forward", allowed) or self.in_scope(rel, "reverse", allowed))}
        key = {eid: (sign * weight(eid, rel), eid) for eid, (_, _, rel) in links.items()}
        chosen = answer["edges"]
        up: dict = {}

        def root(x: Any) -> Any:
            while up.get(x, x) != x:
                up[x] = up.get(up[x], up[x])            # path halving
                x = up[x]
            return x
        for eid in chosen:
            if eid not in links:
                self.bad(f"the forest edge {eid!r} is not a link in scope (or is a loop)")
                return
            a, b = root(links[eid][0]), root(links[eid][1])
            if a == b:
                self.bad(f"the forest edge {eid} closes a cycle")
                return
            up[a] = b
        if answer.get("count") != len(chosen) or answer.get("trees") != len(self.types_of) - len(chosen):
            self.bad("count is the forest's edges and trees the nodes less them")
        total = sum((weight(eid, links[eid][2]) for eid in chosen), Fraction(0) if rational else 0)
        stated = Fraction(answer["weight_exact"]) if rational and isinstance(answer.get("weight_exact"), str) \
            else answer.get("weight")
        if stated != total:
            self.bad(f"the forest weighs {total}, but the answer says {stated!r}")
        adjacent: dict = {}
        for eid in chosen:
            s, t, _ = links[eid]
            adjacent.setdefault(s, []).append((t, key[eid]))
            adjacent.setdefault(t, []).append((s, key[eid]))
        depth: dict = {}
        lift: list[dict] = [{}]                 # lift[j][x]: (the node 2^j hops above x, the largest key on the way)
        for start in adjacent:
            if start in depth:
                continue
            depth[start] = 0
            stack = [start]
            while stack:
                x = stack.pop()
                for y, k in adjacent[x]:
                    if y not in depth:
                        depth[y] = depth[x] + 1
                        lift[0][y] = (x, k)
                        stack.append(y)
        while any(x in lift[-1] and lift[-1][x][0] in lift[-1] for x in lift[-1]):
            prev = lift[-1]
            lift.append({x: (prev[a][0], max(k, prev[a][1])) for x, (a, k) in prev.items() if a in prev})

        def heaviest(u: Any, v: Any) -> Any:
            """The largest key on the tree path between u and v."""
            best = None
            if depth[u] < depth[v]:
                u, v = v, u
            gap, j = depth[u] - depth[v], 0
            while gap:
                if gap & 1:
                    u, k = lift[j][u]
                    best = k if best is None else max(best, k)
                gap, j = gap >> 1, j + 1
            if u == v:
                return best
            for j in range(len(lift) - 1, -1, -1):
                if u in lift[j] and lift[j][u][0] != lift[j][v][0]:
                    best = max(x for x in (best, lift[j][u][1], lift[j][v][1]) if x is not None)
                    u, v = lift[j][u][0], lift[j][v][0]
            return max(x for x in (best, lift[0][u][1], lift[0][v][1]) if x is not None)
        kept = set(chosen)
        for eid, (s, t, _) in sorted(links.items()):
            if eid in kept:
                continue
            if root(s) != root(t):
                self.bad(f"the link {eid} joins two trees: the forest does not span its piece")
            elif not key[eid] > heaviest(s, t):
                self.bad(f"the link {eid} comes before an edge of the tree path between its ends: a lighter forest "
                         "(or one earlier in edge ID) keeps it")

    def cycle_basis(self, request: Mapping, answer: Mapping, status: str) -> None:
        """Each cycle against the records: links in scope, each node met by two of its links (a loop counts two) and
        all of them one piece, its nodes in order around it, its exact weight; the cycles independent over GF(2)
        (by elimination) and, when complete, as many as the cycle space's dimension (links less nodes plus pieces),
        so a basis. With witnesses (de Pina's): cycle i meets witness i oddly and every earlier cycle meets it evenly,
        and no cycle meeting witness i oddly is lighter than cycle i, recomputed here by a shortest path between the
        two copies of a node in the parity graph; then the basis is minimum."""
        if answer.get("rank") is None:
            return
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        weights = request["weights"]
        kind, table = next(iter(weights.items())) if weights else (None, None)
        rational = table is not None and any(isinstance(x, float) for x in table.values())
        links = {eid: (s, t, rel) for eid, (s, t, rel) in self.edge_of.items()
                 if self.in_scope(rel, "forward", allowed) or self.in_scope(rel, "reverse", allowed)}

        def weight(eid: str) -> Any:
            if table is None:
                return 1
            x = table.get(links[eid][2] if kind == "by_relation" else eid)
            if x is None:
                self.bad(f"the link {eid} in scope has no weight in the request")
                return 0
            return Fraction(x) if rational else x

        def stated(owner: Mapping) -> Any:
            if rational:
                return Fraction(owner["weight_exact"]) if isinstance(owner.get("weight_exact"), str) else None
            return owner.get("weight")
        up: dict = {}

        def root(x: Any) -> Any:
            while up.get(x, x) != x:
                up[x] = up.get(up[x], up[x])
                x = up[x]
            return x
        for s, t, _ in links.values():
            a, b = root(s), root(t)
            if a != b:
                up[a] = b
        pieces = len({root(x) for x in self.types_of})
        rank = len(links) - len(self.types_of) + pieces
        if answer["rank"] != rank:
            self.bad(f"the cycle space has dimension {rank}, not {answer['rank']!r}")
        position = {eid: k for k, eid in enumerate(sorted(links))}
        sets = []
        for n, c in enumerate(answer.get("cycles") or ()):
            where, edges = f"cycle {n}", list(c.get("edges") or ())
            if not edges or len(set(edges)) != len(edges) or any(e not in links for e in edges):
                self.bad(f"{where}: its edges are distinct links in scope")
                return
            degree: dict = {}
            for e in edges:
                s, t, _ = links[e]
                degree[s] = degree.get(s, 0) + 1
                degree[t] = degree.get(t, 0) + 1
            nodes = list(c.get("nodes") or ())
            if any(d != 2 for d in degree.values()) or sorted(degree, key=str) != sorted(set(nodes), key=str) \
                    or len(nodes) != len(edges):
                self.bad(f"{where}: not a simple cycle through the nodes it lists")
                return
            unused = list(edges)
            for k, u in enumerate(nodes):                      # consecutive nodes joined by a link of the cycle
                v = nodes[(k + 1) % len(nodes)]
                hit = next((e for e in unused if {links[e][0], links[e][1]} == {u, v}), None)
                if hit is None:
                    self.bad(f"{where}: no link of it joins {u!r} to {v!r}")
                    return
                unused.remove(hit)
            if sum((weight(e) for e in edges), Fraction(0) if rational else 0) != stated(c):
                self.bad(f"{where}: its weight is not the exact sum of its links' weights")
            sets.append(sum(1 << position[e] for e in edges))
        basis: dict[int, int] = {}                             # GF(2) elimination: by highest bit
        for n, bits in enumerate(sets):
            while bits:
                top = bits.bit_length() - 1
                if top not in basis:
                    basis[top] = bits
                    break
                bits ^= basis[top]
            if not bits:
                self.bad(f"cycle {n} is a sum of the cycles before it: they are not independent")
        complete = status == COMPLETED
        if complete and len(sets) != rank:
            self.bad(f"a complete basis has {rank} cycles, not {len(sets)}")
        if complete and stated(answer) != sum((stated(c) for c in answer["cycles"]), Fraction(0) if rational else 0):
            self.bad("the basis weight is not the sum of its cycles' weights")
        cert = answer.get("certificate")
        if cert is None or not self.proofs:
            return
        witnesses = list(cert.get("witnesses") or ())
        if len(witnesses) != len(sets) or any(e not in links for w in witnesses for e in w) \
                or any(len(set(w)) != len(w) for w in witnesses):
            self.bad("a witness of distinct links in scope for each cycle")
            return
        support = [sum(1 << position[e] for e in w) for w in witnesses]
        adjacent: dict = {}
        for eid, (s, t, _) in links.items():
            adjacent.setdefault(s, []).append((eid, t))
            if s != t:
                adjacent.setdefault(t, []).append((eid, s))
        for i, (bits, s) in enumerate(zip(sets, support)):
            if not (bits & s).bit_count() & 1:
                self.bad(f"cycle {i} does not meet its witness oddly")
            for j in range(i):
                if (sets[j] & s).bit_count() & 1:
                    self.bad(f"cycle {j} meets the witness of cycle {i} oddly")
            lightest = None
            odd = {e for e in witnesses[i]}
            for start in sorted({links[e][0] for e in odd} | {links[e][1] for e in odd}, key=str):
                best = {(start, 0): 0}
                heap = [(0, 0, start, 0)]
                order = 0
                while heap:
                    d, _, u, b = heapq.heappop(heap)
                    if d > best.get((u, b), d) or (lightest is not None and d >= lightest):
                        continue
                    if (u, b) == (start, 1):
                        lightest = d
                        break
                    for e, v in adjacent.get(u, ()):
                        state = (v, b ^ (e in odd))
                        nd = d + weight(e)
                        if state not in best or nd < best[state]:
                            best[state] = nd
                            order += 1
                            heapq.heappush(heap, (nd, order, v, state[1]))
            if lightest != stated(answer["cycles"][i]):
                self.bad(f"cycle {i} weighs {stated(answer['cycles'][i])!r}, but the lightest cycle meeting its "
                         f"witness oddly weighs {lightest!r}")

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
        if not isinstance(witness, Mapping) or not isinstance(witness.get("hops"), (list, tuple)):
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
        if not isinstance(entries, (list, tuple)):
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
        joining the step's two bound variables in a scope direction, its child admitted by the step; for an injective
        pattern, no node bound twice and no edge linked twice."""
        match, steps = request["match"], request["steps"]
        for n, row in enumerate(answer.get("rows") or []):
            nodes, links = row["nodes"], row["links"]
            if request.get("injective"):
                bound = [v for v in nodes.values() if v is not None]
                used = [x for x in links if x is not None]
                if len(set(bound)) != len(bound) or len(set(used)) != len(used):
                    self.bad(f"row {n}: an injective pattern's row binds distinct nodes and distinct links")
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
        if cert["kind"] == "feasible_potentials":
            self.feasible_potentials(cert, allowed, sources, targets, kind, table, rational, bound, value)
            return
        if any(x < 0 for x in table.values()):
            self.bad("certificate: potentials that leave a node at the bound prove nothing when a cost is negative")
            return
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

    def feasible_potentials(self, cert: Mapping, allowed: set, sources: set, targets: set, kind: str,
                            table: Mapping, rational: bool, bound: Any, value: dict) -> None:
        """Potentials for costs of any sign: every hop in scope from a listed node leads to a dead end or to a listed
        node whose value is at most the hop's cost above; every hop from a dead end leads to a dead end; every source
        is listed at 0 or below, or a dead end; no target is a dead end, and every listed target is at the bound or
        above. A walk from a source to a target then never meets a dead end (none leads back out) nor an unlisted
        node, and costs at least its target's value less its source's: at least the bound."""
        dead = set(cert["dead_ends"])
        for n in sorted(dead & set(value), key=str):
            self.bad(f"certificate: {n!r} is both listed and a dead end")
        for s in sorted(sources, key=str):
            if s not in dead and (s not in value or value[s] > 0):
                self.bad(f"certificate: the source {s!r} is neither a dead end nor listed at 0 or below")
        for t in sorted(targets, key=str):
            if t in dead:
                self.bad(f"certificate: the target {t!r} is a dead end")
            elif t in value and value[t] < bound:
                self.bad(f"certificate: the target {t!r} has {value[t]}, below the bound {bound}")
        for u in sorted(value, key=str):
            for eid, v, rel in self.hops_from(u, allowed):
                if v in dead:
                    continue
                c = table.get(rel if kind == "by_relation" else eid)
                if c is None:
                    self.bad(f"certificate: the hop {eid} in scope has no cost in the request")
                elif v not in value:
                    self.bad(f"certificate: the hop {eid} leads from {u!r} to {v!r}, which it neither lists nor "
                             "calls a dead end")
                elif value[v] > value[u] + (Fraction(c) if rational else c):
                    self.bad(f"certificate: the hop {eid} from {u!r} ({value[u]}) costs {c} but leads to {v!r} at "
                             f"{value[v]}")
        for u in sorted(dead, key=str):
            for eid, v, _ in self.hops_from(u, allowed):
                if v not in dead:
                    self.bad(f"certificate: the hop {eid} leads from the dead end {u!r} to {v!r}, which is not one")

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
        """A support answer against the records: what stands before and after the failure, from the certificate
        when there is one (its ranks and unfounded nodes checked rule by rule, nothing recomputed), else recomputed
        from the records alone (by re-evaluating each node's rule until nothing changes, not by the engine's
        counting); then the lost and weakened nodes (a prefix of each when an output limit cut the lists), each
        node's supports, the supports that still stand, the hops to those that fell, and the failed nodes it rests
        on. Without proofs, only what an entry says of the records themselves: its supports and failing hops."""
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
        failed = set(request["failed"])
        region, todo = set(failed), deque(failed)
        while todo:
            for u in dependents[todo.popleft()]:
                if u not in region:
                    region.add(u)
                    todo.append(u)
        allowed_dirs = {"forward", "reverse"}
        entries = [("lost", e) for e in answer["lost"]] + [("weakened", e) for e in answer["weakened"]]
        for kind, e in entries:                               # what an entry says of the records themselves
            u, where = e.get("node"), f"{kind} {e.get('node')!r}"
            if u not in supports:
                continue
            if e.get("supports") != len(supports[u]):
                self.bad(f"{where}: {e.get('supports')!r} supports, but the records give {len(supports[u])}")
            for i, h in enumerate(e.get("failing") or []):
                if h.get("from") != u or h.get("direction") not in allowed_dirs:
                    self.bad(f"{where}: failing hop {i} does not start at it")
                    continue
                self.hop(f"{where}: failing hop {i}", u, h.get("to"), h.get("edge"), h.get("relation"),
                         h.get("direction"), allowed)
        if not self.proofs:
            return

        def all_of(u: Any) -> bool:
            return bool(every and self.types_of[u] & every)

        cert = answer.get("certificate")
        if cert is not None:
            stood = self.support_certificate(cert, supports, failed, region, all_of)
            if stood is None:
                return
            before, after = stood
        else:
            def holds(u: Any, stands: set) -> bool:
                sup = supports[u]
                if all_of(u):
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

            before, after = fixpoint(set()), fixpoint(failed)
        fallen = {u for u in region if u in before and u not in after}   # nothing outside the region changes
        want_lost = sorted(u for u in fallen if u not in failed)
        want_weak = sorted(u for u in region if u in after and supports[u] & fallen)

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
        for kind, want, got in (("lost", want_lost, answer["lost"]), ("weakened", want_weak, answer["weakened"])):
            names = [e.get("node") for e in got]
            if names != want[:len(names)] or (status == COMPLETED and len(names) != len(want)):
                self.bad(f"{kind}: the answer lists {names[:10]}, but the records give {want[:10]}")
            for e in got:
                u, where = e.get("node"), f"{kind} {e.get('node')!r}"
                if u not in supports:
                    continue
                standing = len(supports[u] & after)
                if e.get("standing") != standing:
                    self.bad(f"{where}: {e.get('standing')!r} standing supports, but the records give {standing}")
                hops = e.get("failing") or []
                if {h.get("to") for h in hops} != supports[u] & fallen or len(hops) != len(supports[u] & fallen):
                    self.bad(f"{where}: its failing supports are not the ones that fell")
                if e.get("causes") != causes(u):
                    self.bad(f"{where}: causes {e.get('causes')!r}, but the records give {causes(u)}")

    def support_certificate(self, cert: Mapping, supports: Mapping, failed: set, region: set,
                            all_of: Any) -> tuple[set, set] | None:
        """What stands before and after, as a support certificate proves it, or None when it does not.

        For each fixpoint, a node with a rank stands: a ground, or a node whose rule its supports of lower rank meet
        (one of them, or all for an ``all_of`` node); by induction on rank, it is in the least fixpoint. A node
        without one does not: it is failed (after the failure), or it has supports and its rule fails on those
        without a rank (all of them, or one for an ``all_of`` node); such nodes are unfounded, so none is in the
        least fixpoint. The certificate must classify the region (the failed nodes and everything that depends on
        them) and every support of the region; nothing else can change."""
        if not isinstance(cert, Mapping) or cert.get("kind") != "fixpoints" or not isinstance(cert.get("nodes"), (list, tuple)):
            self.bad("certificate: a support answer's certificate is the two fixpoints' ranks (kind fixpoints)")
            return None
        ranks: list[dict] = [{}, {}]                          # before, after: node -> rank or None
        for e in cert["nodes"]:
            u = e.get("node") if isinstance(e, Mapping) else None
            if u not in supports:
                self.bad(f"certificate: {u!r} is not an input node")
                return None
            if u in ranks[0]:
                self.bad(f"certificate: {u!r} is listed twice")
                return None
            for phase, field in enumerate(("before", "after")):
                r = e.get(field)
                if r is not None and (type(r) is not int or r < 0):
                    self.bad(f"certificate: {u!r} has the rank {r!r} {field}: ranks are non-negative integers")
                    return None
                ranks[phase][u] = r
        for u in sorted(region | {v for w in region for v in supports[w]}, key=str):
            if u not in ranks[0]:
                self.bad(f"certificate: it leaves {u!r}, in the failure's region or a support of it, unclassified")
                return None
        for phase, field in enumerate(("before", "after")):
            rank = ranks[phase]
            for u, r in rank.items():
                sup, every = supports[u], all_of(u)
                if phase == 1 and u in failed:
                    if r is not None:
                        self.bad(f"certificate: the failed node {u!r} is ranked as standing after the failure")
                        return None
                    continue
                if r is not None:
                    below = [v in rank and rank[v] is not None and rank[v] < r for v in sup]
                    if sup and not (all(below) if every else any(below)):
                        self.bad(f"certificate: {u!r} has rank {r} {field}, but its supports of lower rank do not "
                                 f"meet its rule")
                        return None
                else:
                    down = [v in rank and rank[v] is None for v in sup]
                    if not sup or not (any(down) if every else all(down)):
                        self.bad(f"certificate: {u!r} has no rank {field}, but its rule can still hold: "
                                 f"{'a ground stands' if not sup else 'a support is ranked or unclassified'}")
                        return None
        return ({u for u, r in ranks[0].items() if r is not None}, {u for u, r in ranks[1].items() if r is not None})

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
        """The witness's hops, each costing what the request gives its edge, and its cost; with a negative cost, a
        negative cycle instead when it gives one, and no lower bound for an interrupted witness. The certificate
        (:meth:`shortest_certificate`) proves the bound the answer states."""
        (kind, table), = request["costs"].items()
        rational = any(isinstance(x, float) for x in table.values())
        signed = any(x < 0 for x in table.values())
        if signed != ("negative_cycle" in answer):
            self.bad("an answer says whether a negative cycle makes it unbounded (negative_cycle) exactly when the "
                     "request gives a negative cost")
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        cycle = answer.get("negative_cycle")
        if cycle is not None:
            self.negative_cycle(request, answer, cycle, status, allowed, kind, table, rational)
            return
        cert = answer.get("certificate")
        if cert is not None:
            self.shortest_certificate(request, cert)
        if answer.get("found") is not True:
            if answer.get("witness") is not None:
                self.bad("only a found witness is given")
            return
        hops = self.chain("witness", answer.get("witness"), set(request["sources"]), set(request["targets"]),
                          [allowed])
        total = self.hop_costs("witness", hops, kind, table, rational)
        reported = self.reported_cost(answer, "cost", rational)
        if total != reported:
            self.bad(f"the hops cost {total}, but the answer says {reported!r}")
        if status == INTERRUPTED:
            low = answer.get("lower_bound_exact") if rational else answer.get("lower_bound")
            low = Fraction(low) if rational and low is not None else low
            if signed:
                if low is not None:
                    self.bad("with a negative cost, an interrupted search knows no lower bound")
            elif low is None or low > reported:
                self.bad("an unproven witness comes with a lower bound no greater than its cost")
            if cert is not None and cert.get("kind") == "potentials" and low is not None \
                    and Fraction(cert["bound"]) != low:
                self.bad(f"the certificate proves the bound {cert['bound']}, not the lower bound {low}")
        elif cert is not None and cert.get("kind") in ("potentials", "feasible_potentials") \
                and Fraction(cert["bound"]) != reported:
            self.bad(f"the certificate proves the bound {cert['bound']}, not the cost {reported}")

    def hop_costs(self, where: str, hops: list, kind: str, table: Mapping, rational: bool) -> Any:
        """The exact sum of the hops' costs, each checked to be what the request gives its edge."""
        total: Any = Fraction(0) if rational else 0
        for i, h in enumerate(hops):
            rec = self.edge_of.get(h.get("edge")) if isinstance(h, Mapping) else None
            if rec is None:
                continue
            want = table.get(rec[2] if kind == "by_relation" else h.get("edge"))
            if h.get("cost") != want or type(h.get("cost")) is not type(want):
                self.bad(f"{where}: hop {i}: cost {h.get('cost')!r}, but the request gives {want!r}")
            if want is not None:
                total += Fraction(want) if rational else want
        return total

    def reported_cost(self, answer: Mapping, field: str, rational: bool) -> Any:
        """A stated cost as an exact number: ``<field>_exact`` (its ``<field>`` the nearest float) in rational mode,
        the integer ``<field>`` otherwise."""
        if rational:
            text = answer.get(f"{field}_exact")
            if not isinstance(text, str):
                self.bad(f"a rational-mode {field} gives {field}_exact")
                return None
            reported = Fraction(text)
            if answer.get(field) != _nearest(reported):
                self.bad(f"{field} {answer.get(field)!r} is not the nearest float to {field}_exact {text}")
            return reported
        reported = answer.get(field)
        if not _is_int(reported):
            self.bad(f"an integer-cost answer gives an integer {field}, not {reported!r}")
        return reported

    def negative_cycle(self, request: Mapping, answer: Mapping, nc: Any, status: str, allowed: set, kind: str,
                       table: Mapping, rational: bool) -> None:
        """A walk from a source to the cycle, the cycle (a closed walk whose hops cost less than nothing, as stated)
        and a walk from it to a target: walks from a source to a target that go around it cost ever less."""
        if status != COMPLETED or answer.get("found") is not True or answer.get("optimal") is not False \
                or answer.get("cost") is not None or answer.get("witness") is not None \
                or answer.get("certificate") is not None:
            self.bad("a negative cycle is a complete answer: found, not optimal, with no cost, witness or certificate")
        if not isinstance(nc, Mapping) or not all(isinstance(nc.get(k), Mapping)
                                                  for k in ("lead_in", "cycle", "lead_out")):
            self.bad("negative_cycle gives lead_in, cycle and lead_out")
            return
        start = nc["cycle"].get("source")
        if nc["cycle"].get("target") != start or not nc["cycle"].get("hops"):
            self.bad("the negative cycle is a closed walk of at least one hop")
        lead_in = self.chain("negative_cycle.lead_in", nc["lead_in"], set(request["sources"]), {start}, [allowed])
        loop = self.chain("negative_cycle.cycle", nc["cycle"], {start}, {start}, [allowed])
        lead_out = self.chain("negative_cycle.lead_out", nc["lead_out"], {start}, set(request["targets"]), [allowed])
        self.hop_costs("negative_cycle.lead_in", lead_in, kind, table, rational)
        self.hop_costs("negative_cycle.lead_out", lead_out, kind, table, rational)
        total = self.hop_costs("negative_cycle.cycle", loop, kind, table, rational)
        reported = self.reported_cost(nc["cycle"], "cost", rational)
        if total != reported:
            self.bad(f"the cycle's hops cost {total}, but it says {reported!r}")
        if not total < 0:
            self.bad(f"the cycle costs {total}, which is not negative")

    def links(self, u: Any, allowed: set) -> list[tuple[str, Any, str]]:
        """(edge, the other end, its relation) for every edge at ``u`` whose relation is in scope in either direction:
        an undirected reading of the scope (an edge from u to itself is listed once, with u as its other end)."""
        both = allowed | {(r, "reverse" if d == "forward" else "forward") for r, d in allowed}
        hops = self.hops_from(u, both)
        seen, out = set(), []
        for eid, v, rel in hops:
            if eid not in seen:
                seen.add(eid)
                out.append((eid, v, rel))
        return out

    def hops_into(self, u: Any, allowed: set) -> list[tuple[str, Any, str]]:
        """(edge, the node whose hop it is, its relation) for every hop in scope that leads to ``u``."""
        flipped = {(r, "reverse" if d == "forward" else "forward") for r, d in allowed}
        return self.hops_from(u, flipped)

    def components(self, request: Mapping, answer: Mapping, status: str) -> None:
        """Components against the records: each listed component's edge count and flags recounted; and, from the
        partition certificate, a proof that every group is a component: its tree (strong: two trees) shows it
        connected, and the groups show nothing outside it belongs with it, being disjoint, closed (no hop leads out of
        them; weak: no link joins two of them) and, for strong components, in an order no hop goes back in. Each
        listed component is a group; a complete answer's groups cover the selection, any group of min_size nodes or
        more that the selection holds is listed, and total counts the selection's groups."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        strong = request["connectivity"] == "strong"
        both = allowed | {(r, "reverse" if d == "forward" else "forward") for r, d in allowed}
        step = (lambda u: self.hops_from(u, allowed)) if strong else (lambda u: self.links(u, allowed))
        comps = answer.get("components") or []
        for c in comps:
            if any(n not in self.types_of for n in c["nodes"]):
                self.bad(f"component {c['nodes'][:3]}: names a node that is not an input node")
                return
        for c in comps:
            member, where = set(c["nodes"]), f"component from {c['nodes'][0]!r}"
            inside, leaves, loop = set(), False, False
            for u in c["nodes"]:
                for eid, v, _ in step(u):
                    if v in member:
                        inside.add(eid)
                        loop = loop or v == u
                    else:
                        leaves = True
            if c["edges"] != len(inside):
                self.bad(f"{where}: {len(inside)} edges in scope join its nodes, not {c['edges']}")
            if strong:
                enters = any(v not in member for u in c["nodes"] for _, v, _ in self.hops_into(u, allowed))
                want = {"cyclic": len(member) > 1 or loop, "initial": not enters, "terminal": not leaves}
                for flag, value in want.items():
                    if c[flag] is not value:
                        self.bad(f"{where}: {flag} is {value}, not {c[flag]}")
        cert = self.proof(answer)
        if cert is None:
            return
        groups = [g["nodes"] for g in cert["groups"]]
        group_of: dict = {}
        for i, g in enumerate(groups):
            for n in g:
                if n not in self.types_of or n in group_of:
                    self.bad(f"certificate: {n!r} is not an input node, or is in two groups")
                    return
                group_of[n] = i
        for i, g in enumerate(groups):
            for u in g:
                for eid, v, _ in step(u):
                    j = group_of.get(v)
                    if j is None or (j < i if strong else j != i):
                        self.bad(f"certificate: edge {eid} leads from group {i} to "
                                 f"{'no group' if j is None else f'group {j}'}: the groups are not "
                                 f"{'closed' if j is None else 'in topological order' if strong else 'separate'}")
                        return
        for c in comps:
            i = group_of.get(c["nodes"][0])
            if i is None or list(groups[i]) != list(c["nodes"]):
                self.bad(f"component from {c['nodes'][0]!r} is not one of the certificate's groups")
        want = request["nodes"]
        if status == COMPLETED:
            selection = set(self.types_of) if want is None else set(want)
            if not selection <= set(group_of):
                self.bad("certificate: the groups of a complete answer cover the selection")
            chosen = [g for g in groups if want is None or set(want).intersection(g)]
            listed = {tuple(c["nodes"]) for c in comps}
            if any(len(g) >= request["min_size"] and tuple(g) not in listed for g in chosen):
                self.bad("a group of min_size nodes or more in the selection is not listed")
            if answer["total"] != len(chosen):
                self.bad(f"total is {len(chosen)} groups, not {answer['total']}")
        for t in cert["groups"]:
            member, root = set(t["nodes"]), t["nodes"][0]
            for key, end in (("out", "from"), ("in", "to")):
                entries = t[key]
                if entries is None:
                    if strong or key == "out":
                        self.bad(f"group from {root!r}: no {key} tree")
                    continue
                where = f"group from {root!r}: the {key} tree"
                parent = {}
                for e in entries:
                    node, h = e["node"], e["via"]
                    if node in parent or node == root or node not in member or h[end] not in member:
                        self.bad(f"{where}: {node!r} is listed twice, is the root, or leaves the component")
                        continue
                    frm, to = (h["from"], node) if end == "from" else (node, h["to"])
                    self.hop(where, frm, to, h["edge"], h["relation"], h["direction"], allowed if strong else both)
                    parent[node] = h[end]
                if set(parent) != member - {root}:
                    self.bad(f"{where} does not span the component")
                if not _acyclic(parent):                    # every chain ends at the root: the tree has no cycle
                    self.bad(f"{where} is circular or detached")

    def biconnectivity(self, request: Mapping, answer: Mapping, status: str) -> None:
        """Biconnectivity against the records: bridges name their edges' records, links of the scope; and, from the
        depth-first forest, every claim. The forest is checked as a depth-first search (each node's edge to its parent
        a link, preorder numbers a preorder of the forest, no link between two nodes of which neither is the other's
        ancestor); a node is finished when every link of its subtree leads into the forest. Low points are recomputed
        from the records for finished nodes, and from them the articulation points (with their splits), bridges and
        blocks: a complete answer lists exactly these, an interrupted one some of them."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}

        def links(u: Any) -> list:
            return [(eid, v) for eid, v, _ in self.links(u, allowed) if v != u]
        for b in answer.get("bridges") or []:
            rec = self.edge_of.get(b["edge"])
            if rec is None or rec != (b["source"], b["target"], b["relation"]) or rec[0] == rec[1] or not (
                    self.in_scope(rec[2], "forward", allowed) or self.in_scope(rec[2], "reverse", allowed)):
                self.bad(f"bridge {b['edge']!r} is not an input edge with these ends and relation, linking two nodes "
                         "in scope")
        cert = self.proof(answer)
        if cert is None:
            return
        pre: dict = {}
        via: dict = {}
        for f in cert["forest"]:
            if f["node"] not in self.types_of or f["node"] in pre or not _is_int(f["pre"]):
                self.bad(f"certificate: {f['node']!r} is not an input node, or is listed twice")
                return
            pre[f["node"]], via[f["node"]] = f["pre"], f["via"]
        if sorted(pre.values()) != list(range(len(pre))):
            self.bad("certificate: preorder numbers are 0, 1, 2, ... once each")
            return
        parent: dict = {}
        children: dict = {u: [] for u in pre}
        for u, eid in via.items():
            if eid is None:
                continue
            rec = self.edge_of.get(eid)
            if rec is None or u not in rec[:2] or rec[0] == rec[1] or not (
                    self.in_scope(rec[2], "forward", allowed) or self.in_scope(rec[2], "reverse", allowed)):
                self.bad(f"certificate: {u!r} hangs from {eid!r}, which is no link at it")
                return
            q = rec[1] if rec[0] == u else rec[0]
            if q not in pre or pre[q] >= pre[u]:
                self.bad(f"certificate: {u!r} hangs from {q!r}, which is not in the forest before it")
                return
            parent[u] = q
            children[q].append(u)
        want = request["nodes"]
        if want is not None and any(u not in parent and u not in want for u in pre):
            self.bad("certificate: a tree of the forest grows from a node the request does not name")
            return
        order = []                                         # the forest's own preorder: it must be the numbering
        for root in sorted((u for u in pre if u not in parent), key=pre.get):
            todo = [root]
            while todo:
                u = todo.pop()
                order.append(u)
                todo.extend(sorted(children[u], key=pre.get, reverse=True))
        if [pre[u] for u in order] != list(range(len(order))) or len(order) != len(pre):
            self.bad("certificate: the numbers are not a preorder of the forest")
            return
        size = {u: 1 for u in pre}
        for u in reversed(order):
            if u in parent:
                size[parent[u]] += size[u]

        def above(a: Any, d: Any) -> bool:                 # a is d or an ancestor of d
            return pre[a] <= pre[d] < pre[a] + size[a]
        done, low = {}, {}
        for u in reversed(order):
            ends = links(u)
            for eid, w in ends:
                if w in pre and not (above(u, w) or above(w, u)):
                    self.bad(f"certificate: link {eid} joins {u!r} and {w!r}, neither above the other: no "
                             "depth-first search leaves such a link")
                    return
            done[u] = all(w in pre for _, w in ends) and all(done[c] for c in children[u])
            if done[u]:
                low[u] = min([pre[u]] + [pre[w] for eid, w in ends if eid != via[u]] +
                             [low[c] for c in children[u]])
        points, bridges = {}, set()
        for u in pre:
            if not done[u]:
                continue
            if u not in parent:
                if len(children[u]) > 1:
                    points[u] = len(children[u])
            else:
                s = sum(1 for c in children[u] if low[c] >= pre[u])
                if s:
                    points[u] = s + 1
                if low[u] > pre[parent[u]]:
                    bridges.add(via[u])
        claimed = {x["node"]: x["splits"] for x in answer.get("articulation_points") or []}
        claimed_bridges = {b["edge"] for b in answer.get("bridges") or []}
        complete = status == COMPLETED
        if any(points.get(u) != k for u, k in claimed.items()) or (complete and claimed != points):
            self.bad(f"articulation points {claimed}, but the forest proves {points}")
        if not claimed_bridges <= bridges or (complete and claimed_bridges != bridges):
            self.bad(f"bridges {sorted(claimed_bridges)}, but the forest proves {sorted(bridges)}")
        if complete:
            if not all(done.values()) or not (set(self.types_of) if want is None else set(want)) <= set(pre):
                self.bad("certificate: the forest of a complete answer is finished and covers the selection")
            if answer.get("components") != len(pre) - len(parent):
                self.bad(f"components is {len(pre) - len(parent)} trees, not {answer.get('components')}")
        if answer.get("blocks") is None:
            return
        head: dict = {}
        for u in order:                                     # the separated child each node's links hang under
            if u in parent:
                head[u] = u if done[u] and low[u] >= pre[parent[u]] else head.get(parent[u])
        blocks: dict = {}
        seen: set = set()
        for u in pre:
            for eid, w in links(u):
                if eid in seen or w not in pre:
                    continue
                seen.add(eid)
                d = u if pre[u] > pre[w] else w
                h = head.get(d)
                if h is not None and done[h]:
                    blocks.setdefault(h, set()).add(eid)
        expected = {tuple(sorted(b)) for b in blocks.values()}
        listed = {tuple(b["edges"]) for b in answer["blocks"]}
        if not listed <= expected or (complete and listed != expected):
            self.bad("the blocks are not those the forest proves")
        for b in answer["blocks"]:
            ends = set()
            for eid in b["edges"]:
                ends.update((self.edge_of.get(eid) or ("?", "?"))[:2])
            if sorted(ends) != list(b["nodes"]):
                self.bad(f"block from {b['edges'][0]!r}: its nodes are its edges' ends")


    def max_flow(self, request: Mapping, answer: Mapping, status: str) -> None:
        """A flow against the records: every edge's flow a hop in scope, within the edge's capacity; at every node
        other than the sources and targets as much in as out (and no more than the node capacity); the value what
        leaves the sources. An interrupted answer's upper bound: the least of the capacities leaving the sources and
        entering the targets, recomputed. A complete answer's cut: each hop and node it lists with the capacity the
        request gives, adding up to the value (so flow and cut prove each other optimal), and exactly the cut nearest
        the sources: what the flow's residual graph reaches from them (no target among it) is its source side, the
        same for every maximum flow, which the certificate must name."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        sources, targets = set(request["sources"]), set(request["targets"])
        terminals = sources | targets
        table = request["capacities"]
        kind, values = next(iter(table.items())) if table is not None else (None, None)

        def capacity(eid: str) -> Any:
            if values is None:
                return Fraction(1)
            x = values.get(self.edge_of[eid][2] if kind == "by_relation" else eid)
            return None if x is None else Fraction(x)
        node_cap = None if request["node_capacity"] is None else Fraction(request["node_capacity"])

        def read(entry: Mapping, field: str) -> Fraction:
            exact = entry.get(f"{field}_exact")
            return Fraction(exact) if exact is not None else Fraction(entry[field])
        balance: dict = {}
        for i, f in enumerate(answer.get("flows") or []):
            where = f"flow {i} ({f.get('edge')!r})"
            self.hop(where, f["from"], f["to"], f["edge"], f["relation"], f["direction"], allowed)
            rec = self.edge_of.get(f["edge"])
            if rec is None:
                continue
            amount, c = read(f, "flow"), capacity(f["edge"])
            if amount <= 0 or c is None or amount > c:
                self.bad(f"{where}: carries {amount}, beyond its capacity {c}")
            balance[f["from"]] = balance.get(f["from"], Fraction(0)) - amount
            balance[f["to"]] = balance.get(f["to"], Fraction(0)) + amount
        for v, b in balance.items():
            if v not in terminals and b != 0:
                self.bad(f"flow is not conserved at {v!r}: {b} more in than out")
        if node_cap is not None:
            inflow: dict = {}
            for f in answer.get("flows") or []:
                inflow[f["to"]] = inflow.get(f["to"], Fraction(0)) + read(f, "flow")
            over = sorted(v for v, x in inflow.items() if v not in terminals and x > node_cap)
            if over:
                self.bad(f"more than the node capacity passes through {over[:5]}")
        value = read(answer, "value")
        sent = -sum((balance.get(s, Fraction(0)) for s in sources), Fraction(0))
        if sent != value:
            self.bad(f"the sources send {sent}, not the value {value}")
        if status != COMPLETED:                          # the bound: what the sources can send, or the targets take
            out_cap = sum((capacity(eid) or 0 for s in sources for eid, v, _ in self.hops_from(s, allowed)
                           if v not in sources), Fraction(0))
            in_cap = sum((capacity(eid) or 0 for t in targets for eid, u, _ in self.hops_into(t, allowed)
                          if u not in targets), Fraction(0))
            if read(answer, "upper_bound") != min(out_cap, in_cap):
                self.bad(f"an interrupted flow's upper bound is {min(out_cap, in_cap)} (the least of what the "
                         f"sources can send and the targets take), not {read(answer, 'upper_bound')}")
            return
        listed = set()
        total = Fraction(0)
        for h in answer["cut"]:
            self.hop(f"cut hop {h['edge']!r}", h["from"], h["to"], h["edge"], h["relation"], h["direction"], allowed)
            if h["edge"] in self.edge_of and read(h, "capacity") != capacity(h["edge"]):
                self.bad(f"cut hop {h['edge']!r}: capacity {read(h, 'capacity')}, not {capacity(h['edge'])}")
            listed.add((h["edge"], h["direction"]))
            total += read(h, "capacity")
        for x in answer["cut_nodes"]:
            if node_cap is None or x["node"] in terminals or read(x, "capacity") != node_cap:
                self.bad(f"cut node {x['node']!r}: no node capacity of {read(x, 'capacity')} applies to it")
            total += read(x, "capacity")
        if total != value:
            self.bad(f"the cut's capacities add up to {total}, not the value {value}")
        # The residual graph of the flow: what it reaches from the sources is the source side of the minimum cut
        # nearest them, the same for every maximum flow. A target among it would leave an augmenting path.
        moved: dict = {}
        through: dict = {}
        for f in answer.get("flows") or []:
            moved[(f["edge"], f["direction"])] = read(f, "flow")
            through[f["to"]] = through.get(f["to"], Fraction(0)) + read(f, "flow")

        def split(v: Any) -> bool:
            return node_cap is not None and v not in terminals

        def inward(v: Any) -> tuple:
            return (v, 0) if split(v) else (v, 1)

        def way(eid: str, u: Any, w: Any) -> str:
            src, dst, _ = self.edge_of[eid]
            return "forward" if (src, dst) == (u, w) else "reverse"
        reached = {(s, 1) for s in sources}
        todo = list(reached)
        while todo:
            v, out = todo.pop()
            steps = []
            if out:                                      # a node, or its outward copy: its hops, and back into it
                for eid, w, _ in self.hops_from(v, allowed):
                    if w != v and (capacity(eid) or 0) > moved.get((eid, way(eid, v, w)), 0):
                        steps.append(inward(w))
                if split(v):
                    if through.get(v, 0) > 0:
                        steps.append((v, 0))
            if not out or not split(v):                  # its inward side: through the node, and back along a flow
                if split(v) and node_cap > through.get(v, 0):
                    steps.append((v, 1))
                for eid, w, _ in self.hops_into(v, allowed):
                    if w != v and moved.get((eid, way(eid, w, v)), 0) > 0:
                        steps.append((w, 1))
            for x in steps:
                if x not in reached:
                    reached.add(x)
                    todo.append(x)
        side = {v for v, out in reached if out}
        if side & targets:
            self.bad(f"the flow is not maximum: its residual graph reaches the target(s) {sorted(side & targets)[:5]}")
            return
        nearest_nodes = {v for v, out in reached if not out and (v, 1) not in reached}
        nearest = set()
        for u in side:
            for eid, w, _ in self.hops_from(u, allowed):
                if w != u and inward(w) not in reached and capacity(eid):
                    nearest.add((eid, way(eid, u, w)))
        if nearest != listed or nearest_nodes != {x["node"] for x in answer["cut_nodes"]}:
            self.bad(f"the cut is not the one nearest the sources, which crosses {sorted(nearest)[:5]} and the nodes "
                     f"{sorted(nearest_nodes)[:5]}")
        cert = self.proof(answer)
        if cert is not None and set(cert["source_side"]) != side:
            self.bad("certificate: the source side is not what the flow's residual graph reaches from the sources")

    def matching(self, request: Mapping, answer: Mapping, status: str) -> None:
        """A matching against the records: every pair a hop in scope from a node of the left set to a node outside
        it, no node in two pairs; left_count the left set's size. König's cover, when given: every candidate hop
        (from a left node to a node outside the left set) touches one of its nodes, and it has as many nodes as there
        are pairs, so no matching has more."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        spec = request["left"]
        left = {n for n in self.types_of if (spec["nodes"] is None or n in spec["nodes"]) and
                (spec["node_types"] is None or self.types_of[n] & set(spec["node_types"]))}
        if answer.get("left_count") != len(left):
            self.bad(f"the left set has {len(left)} nodes, not {answer.get('left_count')}")
        lefts, rights = set(), set()
        for i, x in enumerate(answer.get("pairs") or []):
            where = f"pair {i} ({x.get('left')!r})"
            if x["left"] not in left or x["right"] in left or x["left"] in lefts or x["right"] in rights:
                self.bad(f"{where}: pairs a left node with a node outside the left set, each node once")
            lefts.add(x["left"])
            rights.add(x["right"])
            self.hop(where, x["left"], x["right"], x["edge"], x["relation"], x["direction"], allowed)
        cert = self.proof(answer)
        if cert is None:
            return
        cover = set(cert["nodes"])
        if len(cover) != answer.get("size"):
            self.bad(f"the cover has {len(cover)} nodes, not as many as the {answer.get('size')} pairs")
        for u in sorted(left):
            if u in cover:
                continue
            for eid, v, _ in self.hops_from(u, allowed):
                if v not in left and v not in cover:
                    self.bad(f"certificate: the candidate hop {eid} from {u!r} to {v!r} touches no node of the cover")
                    return


    def dominators(self, request: Mapping, answer: Mapping, status: str) -> None:
        """Dominators against the records. Without a certificate, only that the answer names input nodes. With the
        low-high certificate, Georgiadis and Tarjan's check, arc by arc and without computing dominators: the listed
        parents form a tree whose preorder the list is (a start before everything, joined to each source); every hop
        in scope from a listed node leads to a listed node (the region is closed) whose parent is an ancestor of the
        hop's tail (the parent property); and every listed node has a hop from its parent, or hops from a node before
        it and from a node after it that is not its descendant (the order is low-high). Then the tree is the dominator
        tree, and the answer's tree or chains must be read from it."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        sources = set(request["sources"])
        for e in answer.get("tree") or []:
            if e["node"] not in self.types_of:
                self.bad(f"{e['node']!r} is not an input node")
        cert = self.proof(answer)
        if cert is None:
            return
        order = [e["node"] for e in cert["order"]]
        parent = {e["node"]: e["idom"] for e in cert["order"]}
        if len(parent) != len(order) or any(n not in self.types_of for n in order):
            self.bad("certificate: its nodes are input nodes, each listed once")
            return
        pos = {n: i + 1 for i, n in enumerate(order)}           # the start is 0
        start = object()
        kids: dict = {start: []}
        for n in order:
            kids[n] = []
        for n in order:
            d = parent[n]
            if d is not None and (d not in pos or pos[d] >= pos[n]):
                self.bad(f"certificate: {n!r}'s dominator {d!r} is not listed before it")
                return
            kids[start if d is None else d].append(n)
        walk, seen = [start], []
        while walk:
            x = walk.pop()
            seen.append(x)
            walk.extend(reversed(kids[x]))
        if seen[1:] != order:
            self.bad("certificate: the order is not a preorder of the tree")
            return
        size = {n: 1 for n in order}
        for n in reversed(order):
            if parent[n] is not None:
                size[parent[n]] += size[n]

        def above(a: Any, d: Any) -> bool:                       # a is d or an ancestor of d (None: the start)
            return a is None or pos[a] <= pos[d] < pos[a] + size[a]
        into: dict = {n: [] for n in order}
        for s in sources:
            if s not in pos or parent[s] is not None:
                self.bad(f"certificate: the source {s!r} is not listed, or is given a dominator")
                return
            into[s].append(None)
        for u in order:
            for eid, v, _ in self.hops_from(u, allowed):
                if v == u:
                    continue
                if v not in pos:
                    self.bad(f"certificate: hop {eid} leads from {u!r} to {v!r}, outside the listed region")
                    return
                if not above(parent[v], u):
                    self.bad(f"certificate: hop {eid} from {u!r} reaches {v!r} around its dominator {parent[v]!r}")
                    return
                into[v].append(u)
        for v in order:
            tails = into[v]
            if parent[v] in tails:
                continue
            low = any(u is None or pos[u] < pos[v] for u in tails)
            high = any(u is not None and pos[u] > pos[v] and not above(v, u) for u in tails)
            if not (low and high):
                self.bad(f"certificate: {v!r} has neither a hop from its dominator nor hops from before and after it")
                return
        if answer.get("reached") != len(order):
            self.bad(f"reached is {answer.get('reached')}, but the certificate lists {len(order)} nodes")
        if answer.get("tree") is not None:
            if sorted((e["node"], e["idom"]) for e in answer["tree"]) != sorted(parent.items(), key=lambda x: x[0]):
                self.bad("the tree is not the certificate's")
        for c in answer.get("chains") or []:
            t = c["target"]
            chain, k = [], parent.get(t)
            while k is not None:
                chain.append(k)
                k = parent[k]
            if c["reachable"] is not (t in pos) or list(c["dominators"]) != chain[::-1]:
                self.bad(f"the chain of {t!r} is not the certificate's")


    def critical_path(self, request: Mapping, answer: Mapping, status: str) -> None:
        """A critical path answer against the records. A cycle: its hops, in scope, closing on themselves (and, with
        sources, hops from one to it). An acyclic answer, from its certificate: the order covers what is scheduled
        (every node, or what the sources reach: it holds them, no hop leaves it, and every other node in it has a hop
        from a node before it), every hop between its nodes goes forward in it, and the earliest times satisfy the
        longest-path recurrence exactly (0 with no hop in, else the most over the hops in of the tail's time plus the
        hop's cost), which proves the makespan; the critical path's hops and costs add up to it, and a schedule's
        latest times satisfy the backward recurrence (the makespan with no hop out, else the least over the hops out
        of the head's time less the cost)."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        table = request["costs"]
        kind, values = next(iter(table.items())) if table is not None else (None, None)

        def cost(eid: str) -> Fraction:
            if values is None:
                return Fraction(1)
            x = values.get(self.edge_of[eid][2] if kind == "by_relation" else eid)
            return Fraction(x) if x is not None else Fraction(-1)

        def read(entry: Mapping, field: str) -> Fraction:
            exact = entry.get(f"{field}_exact")
            return Fraction(exact) if exact is not None else Fraction(entry[field])
        if status != COMPLETED:
            return
        if answer["acyclic"] is False:
            cyc = answer["cycle"]
            self.chain("cycle", {"source": cyc["hops"][0]["from"], "target": cyc["hops"][0]["from"],
                                 "length": len(cyc["hops"]), "hops": cyc["hops"]}, {cyc["hops"][0]["from"]}, None,
                       [allowed])
            if cyc["reach"] is not None:
                start = cyc["hops"][0]["from"]
                src = cyc["reach"][0]["from"] if cyc["reach"] else start
                self.chain("reach", {"source": src, "target": start, "length": len(cyc["reach"]),
                                     "hops": cyc["reach"]}, set(request["sources"]), {start}, [allowed])
            return
        for h in (answer.get("path") or {}).get("hops", []):
            self.hop("critical path", h["from"], h["to"], h["edge"], h["relation"], h["direction"], allowed)
            if table is not None and Fraction(h["cost"]) != cost(h["edge"]):
                self.bad(f"critical path: hop {h['edge']!r} costs {cost(h['edge'])}, not {h['cost']}")
        cert = self.proof(answer)
        if cert is None:
            return
        order = [e["node"] for e in cert["order"]]
        pos = {n: i for i, n in enumerate(order)}
        early = {e["node"]: read(e, "earliest") for e in cert["order"]}
        sources = request["sources"]
        if any(n not in self.types_of for n in order) or (sources is None and set(order) != set(self.types_of)) or (
                sources is not None and not set(sources) <= set(pos)):
            self.bad("certificate: the order covers what is scheduled: every node, or the sources and what they reach")
            return
        into: dict = {n: [] for n in order}
        for u in order:
            for eid, v, _ in self.hops_from(u, allowed):
                if v not in pos:
                    self.bad(f"certificate: hop {eid} leads from {u!r} out of the scheduled nodes")
                    return
                if pos[v] <= pos[u]:
                    self.bad(f"certificate: hop {eid} from {u!r} to {v!r} goes back in the order (or is a loop)")
                    return
                into[v].append((u, eid))
        for v in order:
            if sources is not None and v not in sources and not into[v]:
                self.bad(f"certificate: {v!r} is no source and nothing scheduled leads to it")
                return
            want = max((early[u] + cost(eid) for u, eid in into[v]), default=Fraction(0))
            if early[v] != want:
                self.bad(f"certificate: {v!r}'s earliest time is {want}, not {early[v]}")
                return
        span = max(early.values(), default=Fraction(0))
        if read(answer, "cost") != span:
            self.bad(f"the makespan is {span}, not {read(answer, 'cost')}")
        path = answer.get("path")
        if path is not None and (early.get(path["target"]) != span or path["source"] not in pos or any(
                h["from"] not in pos or h["to"] not in pos for h in path["hops"])):
            self.bad("the critical path does not stay among the scheduled nodes and end at one whose earliest time is "
                     "the makespan")
        sched = answer.get("schedule")
        if sched is not None:
            late: dict = {}
            outs: dict = {n: [] for n in order}
            for v in order:
                for u, eid in into[v]:
                    outs[u].append((v, eid))
            for v in reversed(order):
                late[v] = min((late[w] - cost(eid) for w, eid in outs[v]), default=span)
            for e in sched:
                n = e["node"]
                if n not in early or read(e, "earliest") != early[n] or read(e, "latest") != late[n]:
                    self.bad(f"schedule: {n!r} has earliest {early.get(n)} and latest {late.get(n)}")
                    return

    def d_separation(self, request: Mapping, answer: Mapping, status: str) -> None:
        """A d-separation answer against the records. Connected: every step of the trail and of each collider's path
        is a hop in scope (parent to child); the result's own check has the trail's shape. Separated, from the
        certificate: the ancestors hold every given node and every parent of each of theirs; the states hold every
        node of x arrived at going up, and every state a move reaches (from a node not given: down to its children,
        and when it arrived going up, up to its parents; from a node among the ancestors that it arrived at going
        down, up to its parents); no node of y is among them, so no active trail reaches y."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        if status != COMPLETED:
            return
        if answer["separated"] is False:
            for s in answer["trail"]:
                self.hop("trail", s["from"], s["to"], s["edge"], s["relation"], s["direction"], allowed)
            for c in answer["colliders"]:
                for h in c["to_given"]:
                    self.hop(f"collider {c['node']!r}", h["from"], h["to"], h["edge"], h["relation"], h["direction"],
                             allowed)
            return
        cert = self.proof(answer)
        if cert is None:
            return
        given, ancestors = set(request["given"]), set(cert["ancestors"])
        if not given <= ancestors or any(u not in self.types_of for u in ancestors):
            self.bad("certificate: the ancestors hold every given node, and only input nodes")
            return
        for v in ancestors:
            for _, u, _ in self.hops_into(v, allowed):
                if u not in ancestors:
                    self.bad(f"certificate: {u!r}, a parent of {v!r}, is missing from the ancestors")
                    return
        ways = {e["node"]: (e["up"], e["down"]) for e in cert["states"]}
        visited = {(n, w) for n, (up, down) in ways.items() for w, on in ((0, up), (1, down)) if on}
        if any((x, 0) not in visited for x in request["x"]):
            self.bad("certificate: every node of x is a visited state, arrived at going up")
            return
        for v, way in visited:
            moves = []
            if v not in given:
                moves += [(w, 1) for _, w, _ in self.hops_from(v, allowed)]
                if way == 0:
                    moves += [(u, 0) for _, u, _ in self.hops_into(v, allowed)]
            if way == 1 and v in ancestors:
                moves += [(u, 0) for _, u, _ in self.hops_into(v, allowed)]
            for m in moves:
                if m not in visited:
                    self.bad(f"certificate: a move from {v!r} reaches {m[0]!r}, which the states do not hold")
                    return
        reached = sorted(set(request["y"]) & set(ways))
        if reached:
            self.bad(f"certificate: the states reach {reached[:5]} of y: the trail is active")

    def chosen(self, spec: Mapping) -> set:
        """The input nodes a ``{node_types, nodes}`` constraint admits (a type through the hierarchy)."""
        return {n for n in self.types_of if (spec["nodes"] is None or n in spec["nodes"]) and
                (spec["node_types"] is None or self.types_of[n] & set(spec["node_types"]))}

    def incidence(self, request: Mapping, answer: Mapping, status: str) -> None:
        """An incidence answer against the records: the matrix rebuilt from them (every edge in scope, either way,
        joining a row node and a column node, adds its coefficient), every listed vector multiplied by it (the left
        kernel's from the left, the right kernel's from the right) to zero, each listed basis independent (every
        vector nonzero at a node where the others are zero), and, from the certificate, the minor's determinant
        nonzero by Bareiss's fraction-free elimination: the rank is at least the minor's size, and the right kernel's
        vectors bound it from above, so the rank and the kernels' completeness are proven."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        rows, cols = self.chosen(request["rows"]), self.chosen(request["columns"])
        if answer.get("rows") != len(rows) or answer.get("columns") != len(cols):
            self.bad(f"the matrix has {len(rows)} rows and {len(cols)} columns, not {answer.get('rows')} and "
                     f"{answer.get('columns')}")
            return
        if status != COMPLETED:
            return
        (kind, table), = request["coefficients"].items()
        matrix: dict = {}
        for u in rows:
            for eid, v, rel in self.links(u, allowed):
                if v in cols:
                    x = table.get(rel if kind == "by_relation" else eid)
                    if x is None:
                        self.bad(f"edge {eid} in scope has no coefficient")
                        return
                    matrix[(u, v)] = matrix.get((u, v), Fraction(0)) + Fraction(x)

        by_row: dict = {}
        by_col: dict = {}
        for (u, v), a in matrix.items():
            by_row.setdefault(u, []).append((v, a))
            by_col.setdefault(v, []).append((u, a))

        def check(field: str, vecs: list, mine: set, other: set) -> None:
            products: dict = {}
            seen: dict = {}
            line = by_row if mine is rows else by_col
            for i, vec in enumerate(vecs):
                for e in vec:
                    if type(e["value"]) is not int:
                        self.bad(f"{field}: {e['value']!r} is not an integer")
                        return
                    if e["node"] not in mine:
                        self.bad(f"{field}: {e['node']!r} is not one of the matrix's "
                                 f"{'rows' if mine is rows else 'columns'}")
                        return
                    seen.setdefault(e["node"], []).append(i)
                    for o, a in line.get(e["node"], ()):
                        products[(i, o)] = products.get((i, o), Fraction(0)) + a * e["value"]
            if any(products.values()):
                self.bad(f"{field}: a vector does not multiply the matrix to zero")
            alone = {seen[n][0] for n in seen if len(seen[n]) == 1}
            if len(alone) != len(vecs):
                self.bad(f"{field}: the vectors are not shown independent (each needs a node where the others are 0)")
        if answer.get("left_kernel") is not None:
            check("left_kernel", answer["left_kernel"], rows, cols)
        if answer.get("right_kernel") is not None:
            check("right_kernel", answer["right_kernel"], cols, rows)
        cert = self.proof(answer)
        if cert is None:
            return
        kernel = cert.get("kernel")
        if kernel is not None:
            left = kernel["side"] == "left"
            check("certificate kernel", kernel["vectors"], rows if left else cols, cols if left else rows)
        minor = cert["minor"]
        if not set(minor["rows"]) <= rows or not set(minor["columns"]) <= cols:
            self.bad("certificate: the minor's rows and columns are the matrix's")
            return
        if not _nonsingular([[matrix.get((u, v), Fraction(0)) for v in minor["columns"]] for u in minor["rows"]]):
            self.bad("certificate: the minor's determinant is zero: it does not prove the rank")


_PRIME = (1 << 61) - 1


def _nonsingular(m: list[list[Fraction]]) -> bool:
    """Whether a square matrix of rationals has a nonzero determinant. Rows are scaled to integers; elimination modulo
    the prime 2^61 - 1 (sparse, each pivot the sparsest row) finds a nonzero pivot in every column when the
    determinant is nonzero modulo p, which proves it nonzero. Otherwise (a determinant that vanishes modulo p, or
    that is zero) Bareiss's fraction-free elimination decides exactly."""
    rows = []
    for r in m:
        den = 1
        for x in r:
            den = den * x.denominator // gcd(den, x.denominator)
        rows.append([int(x * den) for x in r])
    work = [{j: x % _PRIME for j, x in enumerate(r) if x % _PRIME} for r in rows]
    open_rows = set(range(len(work)))
    full = True
    for c in range(len(work)):
        cand = [i for i in open_rows if c in work[i]]
        if not cand:
            full = False
            break
        i = min(cand, key=lambda k: (len(work[k]), k))
        open_rows.discard(i)
        inv = pow(work[i][c], _PRIME - 2, _PRIME)
        piv = {j: v * inv % _PRIME for j, v in work[i].items()}
        for k in cand:
            if k != i:
                row, f = work[k], work[k][c]
                for j, v in piv.items():
                    x = (row.get(j, 0) - f * v) % _PRIME
                    if x:
                        row[j] = x
                    else:
                        row.pop(j, None)
    if full:
        return True
    return _bareiss(rows)


def _bareiss(rows: list[list[int]]) -> bool:
    """Whether a square integer matrix has a nonzero determinant, by Bareiss's fraction-free elimination (every
    division exact), with row exchanges."""
    rows = [list(r) for r in rows]
    n, prev = len(rows), 1
    for k in range(n):
        piv = next((i for i in range(k, n) if rows[i][k]), None)
        if piv is None:
            return False
        rows[k], rows[piv] = rows[piv], rows[k]
        for i in range(k + 1, n):
            for j in range(k + 1, n):
                rows[i][j] = (rows[i][j] * rows[k][k] - rows[i][k] * rows[k][j]) // prev
            rows[i][k] = 0
        prev = rows[k][k]
    return True


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
