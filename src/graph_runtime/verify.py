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
and cause is compared with it. Patterns, uncertified negatives and a k shortest paths ranking without its
certificate are left to the tests' independent oracles. A malformed result is reported as a problem, never raised.
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
        elif op == "k_shortest_paths":
            check.k_shortest_paths(request, answer, status)
        elif op == "min_cost_flow":
            check.min_cost_flow(request, answer, status)
        elif op == "reachability_pairs":
            check.reachability_pairs(request, answer, status)
        elif op == "datalog":
            check.datalog(request, answer, status)
        elif op == "canonical_form":
            check.canonical_form(request, answer)
        elif op == "isomorphism":
            check.isomorphism(request, answer)
        elif op == "flux_modes":
            check.flux_modes(request, answer, status)
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


DATALOG_CHECK_READS = 5_000_000           # the facts a Datalog closure check reads before it gives up and says so


class _TooLarge(Exception):
    """A Datalog closure check would read more than DATALOG_CHECK_READS facts."""


class _Checker:
    def __init__(self, problems: list[str], edge_of: Mapping, types_of: Mapping, relation_up: dict):
        self.problems, self.edge_of, self.types_of, self.relation_up = problems, edge_of, types_of, relation_up
        self._ends: tuple[dict, dict] | None = None
        self._flipped: tuple[set, set] | None = None
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

    def induced(self, nodes: list, request: Mapping) -> tuple[dict, list]:
        """A node set's graph from the records: each node's color (its types, with their ancestors, or none) and the
        edges whose relation (or one above it) the scope names, either way, with both ends in the set."""
        names = {e["relation"] for e in request["scope"]}
        inside = set(nodes)
        colors = {n: (tuple(sorted(self.types_of.get(n, ()))) if request["node_colors"] == "types" else ())
                  for n in nodes}
        edges = [(eid, s, t, rel) for eid, (s, t, rel) in sorted(self.edge_of.items())
                 if s in inside and t in inside and (rel in names or names & set(self.relation_up.get(rel, ())))]
        return colors, edges

    def missing(self, nodes: Iterable) -> bool:
        gone = sorted(n for n in nodes if n not in self.types_of)
        if gone:
            self.bad(f"the node set names nodes the records do not hold: {gone[:5]}")
        return bool(gone)

    def canonical_form(self, request: Mapping, answer: Mapping) -> None:
        """The encoding against the records: the order a permutation of the node set, and the encoding the set's
        graph relabeled by it (each node's color by position; each edge as [i, j, relation] between positions, the
        smaller first when undirected, sorted), its identity the digest of it. That it is the least encoding over
        every order is not checked here (the search; the oracle tests)."""
        order = answer.get("order")
        if order is None:
            return
        nodes = request["nodes"]
        if self.missing(nodes):
            return
        if sorted(order) != sorted(nodes) or len(set(order)) != len(order):
            self.bad("the order is a permutation of the node set")
            return
        colors, edges = self.induced(nodes, request)
        pos = {n: i for i, n in enumerate(order)}
        listed = []
        for _, s, t, rel in edges:
            i, j = pos[s], pos[t]
            if not request["directed"] and i > j:
                i, j = j, i
            listed.append([i, j, rel])
        want = {"nodes": [list(colors[n]) for n in order], "edges": sorted(listed), "directed": request["directed"]}
        got = answer.get("encoding") or {}
        if any(not (_is_int(e[0]) and _is_int(e[1])) for e in got.get("edges") or () if len(e) == 3):
            self.bad("the encoding's positions are integers")
            return
        if [list(x) for x in got.get("nodes") or ()] != want["nodes"] or \
                [list(x) for x in got.get("edges") or ()] != want["edges"] or got.get("directed") != want["directed"]:
            self.bad("the encoding is not the node set's graph relabeled by the order")
            return
        from ._canonical import digest
        if answer.get("canonical_id") != digest({"kind": "graph_runtime.canonical_form", **want}):
            self.bad("the canonical_id is not the digest of the encoding")

    def isomorphism(self, request: Mapping, answer: Mapping) -> None:
        """An isomorphism against the records: the node mapping a bijection from the left set onto the right one that
        keeps colors, and the edge mapping a bijection between their graphs' edges that keeps relations and maps ends
        to mapped ends (in order when directed). With the certificate, a non-isomorphism: other node or edge counts,
        or color refinement of the two graphs side by side giving them other color histograms after the rounds it
        names (recomputed here)."""
        if self.missing(request["left"]) or self.missing(request["right"]):
            return
        left_colors, left_edges = self.induced(request["left"], request)
        right_colors, right_edges = self.induced(request["right"], request)
        if answer.get("isomorphic") is True:
            pairs = [(x.get("left"), x.get("right")) for x in answer.get("mapping") or ()]
            m = dict(pairs)
            if len(m) != len(pairs) or set(m) != set(left_colors) or sorted(m.values()) != sorted(right_colors) \
                    or any(left_colors[a] != right_colors.get(b) for a, b in m.items()):
                self.bad("the mapping is a bijection from the left set onto the right one that keeps colors")
                return
            epairs = [(x.get("left"), x.get("right")) for x in answer.get("edge_mapping") or ()]
            em = dict(epairs)
            lrec = {e[0]: e for e in left_edges}
            rrec = {e[0]: e for e in right_edges}
            if len(em) != len(epairs) or set(em) != set(lrec) or sorted(em.values()) != sorted(rrec):
                self.bad("the edge mapping is a bijection between the two graphs' edges")
                return
            for a, b in em.items():
                _, s, t, rel = lrec[a]
                _, s2, t2, rel2 = rrec[b]
                ends = (m[s], m[t]) == (s2, t2) if request["directed"] else sorted((m[s], m[t])) == sorted((s2, t2))
                if rel != rel2 or not ends:
                    self.bad(f"the edge mapping sends {a} to {b}, which another relation or other ends hold")
                    return
            return
        cert = self.proof(answer)
        if cert is None or answer.get("isomorphic") is not False:
            return
        if cert.get("kind") == "counts":
            if len(left_colors) == len(right_colors) and len(left_edges) == len(right_edges):
                self.bad("certificate: the two graphs have the same numbers of nodes and edges")
            return
        rounds = cert.get("rounds")
        if not _is_int(rounds) or rounds < 0 or rounds > len(left_colors) + len(right_colors):
            self.bad("certificate: refinement names a number of rounds")
            return
        keys = [("L", n) for n in request["left"]] + [("R", n) for n in request["right"]]
        color = {k: (left_colors if k[0] == "L" else right_colors)[k[1]] for k in keys}
        out: dict = {k: [] for k in keys}
        inn: dict = {k: [] for k in keys}
        for side, edges in (("L", left_edges), ("R", right_edges)):
            for _, s, t, rel in edges:
                out[(side, s)].append(((side, t), rel))
                if request["directed"]:
                    inn[(side, t)].append(((side, s), rel))
                elif s != t:
                    out[(side, t)].append(((side, s), rel))
        for _ in range(rounds):
            sig = {k: (color[k], tuple(sorted((color[w], r) for w, r in out[k])),
                       tuple(sorted((color[w], r) for w, r in inn[k]))) for k in keys}
            rank = {s: i for i, s in enumerate(sorted(set(sig.values())))}
            color = {k: rank[sig[k]] for k in keys}
        if sorted(color[k] for k in keys if k[0] == "L") == sorted(color[k] for k in keys if k[0] == "R"):
            self.bad(f"certificate: after {rounds} rounds of refinement the two graphs have the same colors")

    def datalog(self, request: Mapping, answer: Mapping, status: str) -> None:
        """Each listed fact's derivation against the records: its rule, its head matching the fact and each body atom
        matching its premise under one substitution, each premise an input fact the records hold (node, type with
        the hierarchy, edge with every relation above its own) or a fact listed before it at a lower rank; each row
        a fact of the query's predicate matching the query, with that derivation when derivations are asked for.
        With the certificate (the whole model): every rule, fired over the inputs and the listed facts, derives only
        listed facts, so they are the least model, and a complete answer lists every match of the query in it."""
        def term(x: Any) -> tuple:
            if isinstance(x, Mapping):
                return ("c", x.get("const"))
            return ("v", x) if isinstance(x, str) and x.startswith("?") else ("c", x)
        rules = {r["id"]: ((r["head"]["predicate"], [term(a) for a in r["head"]["args"]]),
                           [(b["predicate"], [term(a) for a in b["args"]]) for b in r["body"]])
                 for r in request["program"]}
        def holds(pred: str, args: tuple) -> bool:
            """An input fact, read from the records directly."""
            if pred == "node":
                return len(args) == 1 and args[0] in self.types_of
            if pred == "type":
                return len(args) == 2 and args[1] in self.types_of.get(args[0], ())
            if pred == "edge" and len(args) == 4:
                rec = self.edge_of.get(args[0])
                return rec is not None and rec[:2] == args[1:3] and (
                    args[3] == rec[2] or args[3] in self.relation_up.get(rec[2], ()))
            return False

        def unify(sub: dict, atom: tuple, args: tuple) -> bool:
            if len(atom[1]) != len(args):
                return False
            for (kind, x), v in zip(atom[1], args):
                if kind == "c":
                    if x != v:
                        return False
                elif sub.setdefault(x, v) != v:
                    return False
            return True
        facts = list(answer.get("facts") or ())
        known: dict = {}
        for i, f in enumerate(facts):
            where = f"fact {i}"
            item = (f.get("predicate"), tuple(f.get("args") or ()))
            if item in known:
                self.bad(f"{where}: listed twice")
                return
            known[item] = i
            rule = rules.get(f.get("rule"))
            premises = list(f.get("premises") or ())
            if rule is None or rule[0][0] != item[0] or len(premises) != len(rule[1]):
                self.bad(f"{where}: rule {f.get('rule')!r} does not derive {item[0]!r} from {len(premises)} premises")
                continue
            sub: dict = {}
            ok = unify(sub, rule[0], item[1])
            for atom, prem in zip(rule[1], premises):
                if "fact" in prem:
                    k = prem["fact"]
                    if not (isinstance(k, int) and 0 <= k < i) or facts[k].get("predicate") != atom[0] \
                            or not facts[k].get("rank", 0) < f.get("rank", 0):
                        self.bad(f"{where}: a premise is not a {atom[0]} fact listed before it at a lower rank")
                        ok = False
                        break
                    args = tuple(facts[k].get("args") or ())
                else:
                    args = tuple(prem.get("args") or ())
                    if prem.get("input") != atom[0] or not holds(atom[0], args):
                        self.bad(f"{where}: the input {atom[0]}{args} is not in the records")
                        ok = False
                        break
                ok = ok and unify(sub, atom, args)
            if not ok:
                self.bad(f"{where}: its rule, with one substitution, does not give it from its premises")
        qpred, qterms = request["query"]["predicate"], [term(a) for a in request["query"]["args"]]

        def matches(args: tuple) -> bool:
            return unify({}, (qpred, qterms), args)
        rows = list(answer.get("rows") or ())
        values = [tuple(r.get("values") or ()) for r in rows]
        if any(a >= b for a, b in zip(values, values[1:])) or not all(matches(v) for v in values):
            self.bad("the rows are distinct facts matching the query, in ascending order")
        for r, v in zip(rows, values):
            k = r.get("fact")
            if request["derivations"]:
                if not (isinstance(k, int) and 0 <= k < len(facts)) or (facts[k].get("predicate"),
                                                                         tuple(facts[k].get("args") or ())) != (qpred, v):
                    self.bad(f"the row {list(v)} names no derivation of itself")
            elif k is not None:
                self.bad("rows name derivations only when they are asked for")
        if self.proof(answer) is None:
            return
        budget = [DATALOG_CHECK_READS]                           # what the closure check may read, then it says so

        def spend(k: int) -> None:
            budget[0] -= k
            if budget[0] < 0:
                raise _TooLarge
        model: dict = {}
        try:
            used = {atom[0] for _, body in rules.values() for atom in body}
            if "node" in used:
                spend(len(self.types_of))
                model["node"] = {(n,) for n in self.types_of}
            if "type" in used:
                model["type"] = set()
                for n, types in self.types_of.items():
                    spend(1 + len(types))
                    model["type"].update((n, t) for t in types)
            if "edge" in used:
                model["edge"] = set()
                for eid, (src, dst, rel) in self.edge_of.items():
                    above = self.relation_up.get(rel, ())
                    spend(1 + len(above))
                    model["edge"].update((eid, src, dst, r) for r in {rel} | set(above))
        except _TooLarge:
            self.bad(f"certificate: closing the model needs more than {DATALOG_CHECK_READS} reads; not checked")
            return
        for pred, args in known:
            model.setdefault(pred, set()).add(args)
        indexes: dict = {}

        def candidates(pred: str, positions: tuple, key: tuple) -> Any:
            index = indexes.get((pred, positions))
            if index is None:
                facts = model.get(pred, ())
                spend(len(facts))
                index = indexes[(pred, positions)] = {}
                for args in facts:
                    index.setdefault(tuple(args[k] for k in positions), []).append(args)
            found = index.get(key, ())
            spend(len(found))
            return found
        try:
            self._close(rules, model, candidates, unify)
        except _TooLarge:
            self.bad(f"certificate: closing the model needs more than {DATALOG_CHECK_READS} reads; not checked")
            return
        qmodel = model.get(qpred, ())
        everything = sorted(a for a in qmodel if matches(a))
        if status == COMPLETED and (answer.get("total") != len(everything) or values != everything[:len(values)]):
            self.bad("the rows are the first matches of the query in the model, and total counts them all")

    def _close(self, rules: dict, model: dict, candidates: Any, unify: Any) -> None:
        """That every rule, fired over the model, derives only facts in it (a problem for the first that does not)."""
        for rid, (head, body) in rules.items():                 # closed: every binding's head is in the model
            order, bound, left = [], set(), set(range(len(body)))
            score = [sum(1 for kind, _ in atom[1] if kind == "c") for atom in body]
            places: dict = {}
            for a, atom in enumerate(body):
                for kind, x in atom[1]:
                    if kind == "v":
                        places.setdefault(x, []).append(a)
            queue = [(-score[a], a) for a in range(len(body))]
            heapq.heapify(queue)
            while left:                                         # the atom with the most terms bound next
                neg, j = heapq.heappop(queue)
                if j not in left or -neg != score[j]:
                    continue
                left.discard(j)
                order.append((body[j], tuple(k for k, (kind, x) in enumerate(body[j][1])
                                             if kind == "c" or x in bound)))
                for kind, x in body[j][1]:
                    if kind == "v" and x not in bound:
                        bound.add(x)
                        for a in places[x]:
                            if a in left:
                                score[a] += 1
                                heapq.heappush(queue, (-score[a], a))
            s: dict = {}                                        # one substitution, bound and unbound in place

            def opened(depth: int) -> list:
                atom, positions = order[depth]
                key = tuple(x if kind == "c" else s[x] for kind, x in (atom[1][k] for k in positions))
                return [iter(candidates(atom[0], positions, key)), []]      # candidates, variables this level bound
            frames = [opened(0)] if order else []
            if not order:
                head_args = tuple(x for _, x in head[1])
                if head_args not in model.get(head[0], ()):
                    self.bad(f"certificate: rule {rid} states {head[0]}{head_args}, which the model leaves out")
                    return
            while frames:
                depth = len(frames) - 1
                it, mine = frames[depth]
                for x in mine:
                    del s[x]
                mine.clear()
                args = next(it, None)
                if args is None:
                    frames.pop()
                    continue
                atom = order[depth][0]
                ok = len(atom[1]) == len(args)
                for (kind, x), v in zip(atom[1], args) if ok else ():
                    if kind == "c":
                        ok = x == v
                    elif x in s:
                        ok = s[x] == v
                    else:
                        s[x] = v
                        mine.append(x)
                    if not ok:
                        break
                if not ok:
                    continue
                if depth + 1 < len(order):
                    frames.append(opened(depth + 1))
                    continue
                head_args = tuple(x if kind == "c" else s[x] for kind, x in head[1])
                if head_args not in model.get(head[0], ()):
                    self.bad(f"certificate: rule {rid} derives {head[0]}{head_args}, which the model leaves out")
                    return

    def reachability_pairs(self, request: Mapping, answer: Mapping, status: str) -> None:
        """Each pair against the records: the request's pairs in its order (a prefix when interrupted), counted; a
        node reaching itself by the empty walk; a reachable pair's witness, when asked for, a path of hops in scope
        from its source to its target visiting no node twice. With the certificate, each source of an unreachable
        pair has its closure: a set of input nodes holding the source, from which every hop in scope stays inside,
        and which leaves out every target the answer says the source cannot reach. Without it, the negatives are
        not checked here (the index's completeness; the oracle tests)."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        want = [(x["source"], x["target"]) for x in request["pairs"]]
        pairs = list(answer.get("pairs") or ())
        got = [(x.get("source"), x.get("target")) if isinstance(x, Mapping) else None for x in pairs]
        if got != want[:len(got)] or (status == COMPLETED and len(got) != len(want)):
            self.bad("the pairs are the request's, in its order: all of them, or a prefix when interrupted")
            return
        for n, x in enumerate(pairs):
            where, s, t = f"pair {n}", x["source"], x["target"]
            if s not in self.types_of or t not in self.types_of:
                self.bad(f"{where}: {s!r} and {t!r} must be input nodes")
                continue
            reachable, witness = x.get("reachable"), x.get("witness")
            if reachable is not True and reachable is not False:
                self.bad(f"{where}: reachable is true or false")
            elif s == t and reachable is not True:
                self.bad(f"{where}: a node reaches itself, by the empty walk")
            if reachable is True and request["witnesses"]:
                hops = self.chain(where, witness, {s}, {t}, [allowed])
                visited = [s] + [h.get("to") for h in hops if isinstance(h, Mapping)]
                if len(set(visited)) != len(visited):
                    self.bad(f"{where}: the witness visits a node twice")
            elif witness is not None:
                self.bad(f"{where}: only a reachable pair has a witness, and only when witnesses are asked for")
        if answer.get("count") != len(pairs) or answer.get("reachable") != sum(x.get("reachable") is True
                                                                                for x in pairs):
            self.bad("count is the number of pairs listed, and reachable the number reachable")
        cert = self.proof(answer)
        if cert is None:
            return
        closures = {}
        for c in cert.get("closures") or ():
            closures[c.get("source")] = c.get("nodes")
        negatives = {x["source"] for x in pairs if x.get("reachable") is False}
        if set(closures) != negatives or len(closures) != len(cert.get("closures") or ()):
            self.bad("certificate: one closure for each source of an unreachable pair, and no other")
            return
        for s, nodes in closures.items():
            inside = set(nodes) if isinstance(nodes, (list, tuple)) else set()
            if s not in inside or any(u not in self.types_of for u in inside):
                self.bad(f"certificate: the closure of {s!r} holds it, and only input nodes")
                continue
            for u in sorted(inside, key=str):
                leak = next((h for h in self.hops_from(u, allowed) if h[1] not in inside), None)
                if leak is not None:
                    self.bad(f"certificate: the closure of {s!r} is not closed: {leak[0]} leads from {u!r} to "
                             f"{leak[1]!r}")
                    break
        inside_of = {s: set(nodes) if isinstance(nodes, (list, tuple)) else set() for s, nodes in closures.items()}
        for x in pairs:
            if x.get("reachable") is False and x["target"] in inside_of[x["source"]]:
                self.bad(f"certificate: {x['target']!r} is in the closure of {x['source']!r}, which reaches it")

    def min_cost_flow(self, request: Mapping, answer: Mapping, status: str) -> None:
        """A flow against the records: each hop in scope in its direction carrying at most its edge's capacity, the
        other nodes conserving it, no source taking in and no target giving out more than it passes on, the value the
        sources' net output and the cost the exact sum of flow times cost. With the certificate: the potentials leave
        no arc of the residual network negative once reduced (a hop with room left, a hop with flow backwards at
        minus its cost, the virtual source to every source and back from one that sends, every target to the virtual
        sink and back from one that receives), so no flow of the same value is cheaper; and, when the flow claims to
        be a maximum one, the cut's source side holds every source and no target, every hop out of it is full and
        every hop into it empty, so no flow is larger."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        capacities, costs, value = request["capacities"], request["costs"], request["value"]
        ckind, ctable = next(iter(capacities.items())) if capacities else (None, None)
        (wkind, wtable), = costs.items()
        rational = isinstance(value, float) or any(isinstance(x, float) for t in (ctable or {}, wtable)
                                                   for x in t.values())
        number = (lambda x: Fraction(x)) if rational else (lambda x: x)
        sources, targets = set(request["sources"]), set(request["targets"])

        def read(owner: Mapping, field: str) -> Any:
            if rational:
                return Fraction(owner[f"{field}_exact"]) if isinstance(owner.get(f"{field}_exact"), str) else None
            return owner.get(field)

        def capacity(eid: str, rel: str) -> Any:
            if ctable is None:
                return 1
            x = ctable.get(rel if ckind == "by_relation" else eid)
            return number(x) if x is not None else None

        def price(eid: str, rel: str) -> Any:
            x = wtable.get(rel if wkind == "by_relation" else eid)
            return number(x) if x is not None else None
        hops = []                                         # (edge, direction, from, to, relation): every hop in scope
        for eid, (s, t, rel) in self.edge_of.items():
            if s == t:
                continue
            for direction, a, b in (("forward", s, t), ("reverse", t, s)):
                if self.in_scope(rel, direction, allowed):
                    hops.append((eid, direction, a, b, rel))
        known = {(h[0], h[1]): h for h in hops}
        flow: dict = {}
        balance: dict = {}
        total = Fraction(0) if rational else 0
        for i, f in enumerate(answer.get("flows") or ()):
            hop = known.get((f.get("edge"), f.get("direction")))
            amount = read(f, "flow")
            if hop is None or (f.get("from"), f.get("to"), f.get("relation")) != (hop[2], hop[3], hop[4]) \
                    or (hop[0], hop[1]) in flow:
                self.bad(f"flow {i}: not a hop in scope from {f.get('from')!r} to {f.get('to')!r} by its edge's "
                         "relation, once")
                return
            cap, cost = capacity(hop[0], hop[4]), price(hop[0], hop[4])
            if cap is None or cost is None or amount is None or not 0 < amount <= cap:
                self.bad(f"flow {i}: carries {amount!r}, beyond its capacity {cap!r} (or no capacity or cost)")
                return
            flow[(hop[0], hop[1])] = amount
            balance[hop[2]] = balance.get(hop[2], 0) + amount
            balance[hop[3]] = balance.get(hop[3], 0) - amount
            total += amount * cost
        for node, out in balance.items():
            if node not in sources and node not in targets and out != 0:
                self.bad(f"the flow is not conserved at {node!r}")
            if (node in sources and out < 0) or (node in targets and out > 0):
                self.bad(f"{node!r} takes in or gives out more than a source or a target can")
        sent = sum((balance.get(s, 0) for s in sources), Fraction(0) if rational else 0)
        if read(answer, "value") != sent or read(answer, "cost") != total:
            self.bad(f"the flow sends {sent} at cost {total}, not what the answer says")
        if value is not None and sent > number(value):
            self.bad(f"the flow sends {sent}, more than the {value} asked for")
        cert = self.proof(answer)
        if cert is None:
            return
        pi = {e["node"]: number(e["value"]) for e in cert.get("potentials") or ()}
        top, bottom = number(cert["source"]), number(cert["sink"])
        missing = sorted({x for h in hops for x in h[2:4]} - set(pi) | (sources | targets) - set(pi), key=str)
        if missing:
            self.bad(f"certificate: no potential for {missing[:5]}")
            return

        def arc(where: str, reduced: Any) -> None:
            if reduced < 0:
                self.bad(f"certificate: the residual arc {where} costs {reduced} once reduced")
        for eid, direction, a, b, rel in hops:
            f, cap, cost = flow.get((eid, direction), 0), capacity(eid, rel), price(eid, rel)
            if f < cap:
                arc(f"{a!r} -> {b!r} by {eid}", cost + pi[a] - pi[b])
            if f > 0:
                arc(f"{b!r} -> {a!r} back by {eid}", -cost + pi[b] - pi[a])
        for s in sorted(sources, key=str):
            arc(f"source -> {s!r}", top - pi[s])
            if balance.get(s, 0) > 0:
                arc(f"{s!r} -> source", pi[s] - top)
        for t in sorted(targets, key=str):
            arc(f"{t!r} -> sink", pi[t] - bottom)
            if balance.get(t, 0) < 0:
                arc(f"sink -> {t!r}", bottom - pi[t])
        side = cert.get("source_side")
        if (side is not None) != (value is None or answer.get("feasible") is False):
            self.bad("certificate: a cut proves the flow a maximum one exactly when it claims to be")
        elif side is not None:
            inside = set(side)
            if not sources <= inside or targets & inside:
                self.bad("certificate: the cut's source side holds every source and no target")
            for eid, direction, a, b, rel in hops:
                f = flow.get((eid, direction), 0)
                if a in inside and b not in inside and f != capacity(eid, rel):
                    self.bad(f"certificate: the hop {eid} leaves the cut's source side with room left")
                if a not in inside and b in inside and f != 0:
                    self.bad(f"certificate: the hop {eid} brings flow back into the cut's source side")

    def k_shortest_paths(self, request: Mapping, answer: Mapping, status: str) -> None:
        """Each path against the records: hops in scope chaining from a source to a target, no node twice, each hop at
        the cost the request gives its edge and the path at their exact sum; the paths distinct and in the strict
        order of (cost, hops, start node and edge IDs); no more than k, and exactly k unless the answer says fewer
        exist. That no simple path missing from the list comes before its last is proven by the certificate, when
        the answer carries one (:meth:`k_paths_certificate`); without it, it is Yen's theorem, left to the oracle
        tests."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        costs = request["costs"]
        kind, table = next(iter(costs.items())) if costs else (None, None)
        rational = table is not None and any(isinstance(x, float) for x in table.values())
        sources, targets = set(request["sources"]), set(request["targets"])
        keys = []
        for n, path in enumerate(answer.get("paths") or ()):
            where = f"path {n}"
            hops = self.chain(where, path, sources, targets, [allowed])
            visited = [path.get("source")] + [h.get("to") for h in hops if isinstance(h, Mapping)]
            if len(set(visited)) != len(visited):
                self.bad(f"{where}: visits a node twice")
            if table is None:
                total = len(hops)
                for i, h in enumerate(hops):
                    if h.get("cost") != 1 or type(h.get("cost")) is not int:
                        self.bad(f"{where}: hop {i} costs 1 when the request gives no costs")
            else:
                total = self.hop_costs(where, hops, kind, table, rational)
            if total != self.reported_cost(path, "cost", rational):
                self.bad(f"{where}: its hops cost {total}, not what it says")
            keys.append((total, len(hops), (path.get("source"),) + tuple(h.get("edge") for h in hops)))
        if any(a >= b for a, b in zip(keys, keys[1:])):
            self.bad("the paths are distinct and in the order of (cost, hops, start node and edge IDs)")
        k, count = request["k"], len(keys)
        exhausted = answer.get("exhausted")
        if count > k or (status == COMPLETED and exhausted is not (count < k)):
            self.bad(f"a complete answer lists k paths, or fewer when that is all there are (exhausted)")
        cert = self.proof(answer)
        if cert is not None and status == COMPLETED and not self.problems:
            self.k_paths_certificate(request, answer["paths"], keys, cert)

    def k_paths_certificate(self, request: Mapping, paths: list, keys: list, cert: Mapping) -> None:
        """The ranking's proof. Potentials: every target listed at (0, 0) or below, no hop in scope from a listed node
        lowering them by more than its (cost, 1), and every hop in scope into a listed node from a listed node (so an
        unlisted node reaches no target). Then the tree of the listed paths' prefixes: every simple path not listed
        leaves it at a prefix (or, at the start, from a source no listed path starts at) by a hop no listed path with
        that prefix takes, since a prefix that ends at a target is listed (it comes before the paths it starts). For
        each prefix, every such hop, and every hop from a node its labels list, that avoids the prefix's nodes leads
        to a node whose potential puts it at or after the last path (or that reaches no target), or to a labelled node
        no later than the hop brings it; and no labelled node is a target. A walk before the last path would carry
        labels to its target, so none exists: the listed paths are the first ones."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        costs = request["costs"]
        kind, table = next(iter(costs.items())) if costs else (None, None)
        rational = table is not None and any(isinstance(x, float) for x in table.values())
        sources, targets = set(request["sources"]), set(request["targets"])

        def number(x: Any) -> Any:
            if rational and isinstance(x, str):
                return Fraction(x)
            if not rational and _is_int(x):
                return x
            raise ValueError(f"{x!r} is not a cost as this request writes them")

        def price(eid: str, rel: str) -> Any:
            if table is None:
                return 1
            c = table.get(rel if kind == "by_relation" else eid)
            if c is None:
                raise ValueError(f"the hop {eid} in scope has no cost in the request")
            return Fraction(c) if rational else c

        pi: dict = {}
        for e in cert["potentials"]:
            if e["node"] not in self.types_of or e["node"] in pi or not _is_int(e["hops"]):
                self.bad(f"certificate: the potential of {e['node']!r} is not one of an input node, once")
                return
            pi[e["node"]] = (number(e["cost"]), e["hops"])
        for t in sorted(targets, key=str):
            if t not in pi or pi[t] > (0, 0):
                self.bad(f"certificate: the target {t!r} is not listed at (0, 0) or below")
        for v in sorted(pi, key=str):
            for eid, u, rel in self.hops_into(v, allowed):
                if u not in pi:
                    self.bad(f"certificate: {u!r} reaches {v!r} by the hop {eid}, but is not listed")
                elif pi[u] > (price(eid, rel) + pi[v][0], 1 + pi[v][1]):
                    self.bad(f"certificate: the hop {eid} from {u!r} {pi[u]} leads to {v!r} {pi[v]} for less")
        if self.problems:
            return
        full = len(paths) == request["k"]
        tau = (keys[-1][0], keys[-1][1], 1) if full else None
        last = keys[-1][2] if full else None
        sides = {"before": 0, "after": 1} if full else {None: 0}

        def pruned(val: tuple, v: Any) -> bool:
            bound = pi.get(v)
            return bound is None or (tau is not None and (val[0] + bound[0], val[1] + bound[1], val[2]) >= tau)

        explored: dict = {}
        for e in cert["explored"]:
            labels = {}
            for x in e["labels"]:
                if x["node"] not in self.types_of or x["node"] in labels or not _is_int(x["hops"]) \
                        or x["side"] not in sides:
                    self.bad(f"certificate: the label of {x['node']!r} is not one of an input node, once, with a "
                             f"side {'before or after the last path' if full else 'null (no path is last)'}")
                    return
                labels[x["node"]] = (number(x["cost"]), x["hops"], sides[x["side"]])
            if (e["path"], e["at"]) in explored:
                self.bad(f"certificate: two entries explore {(e['path'], e['at'])}")
                return
            explored[(e["path"], e["at"])] = labels

        def explore(where: str, labels: dict, starts: list, avoid: set) -> None:
            for v in sorted(set(labels) & avoid, key=str):
                self.bad(f"certificate: {where} labels {v!r}, which its prefix visits")
            for val, v, eid in starts:
                if not pruned(val, v) and not (v in labels and labels[v] <= val):
                    self.bad(f"certificate: {where}: the hop {eid} leaves the listed paths for {v!r} at {val}, "
                             "which is neither labelled so nor at or after the last path")
            for y in sorted(labels, key=str):
                if y in targets:
                    self.bad(f"certificate: {where} labels the target {y!r}: a path comes before the last one")
                c, h, b = labels[y]
                for eid, z, rel in self.hops_from(y, allowed):
                    if z in avoid:
                        continue
                    val = (c + price(eid, rel), h + 1, b)
                    if not pruned(val, z) and not (z in labels and labels[z] <= val):
                        self.bad(f"certificate: {where}: the hop {eid} from {y!r} leads to {z!r} at {val}, which "
                                 "is neither labelled so nor at or after the last path")

        used = {x["source"] for x in paths}
        explore("the start", explored.pop((None, None), {}),
                [((0, 0, int(full and s > last[0])), s, None) for s in sorted(sources - used, key=str)], set())
        roots: dict = {}                                  # the prefix tree: [hops taken next, a path stops, seen]
        along = []
        for x in paths:
            at = roots.setdefault(x["source"], [{}, False, False])
            nodes = [at]
            for h in x["hops"]:
                at = at[0].setdefault(h["edge"], [{}, False, False])
                nodes.append(at)
            at[1] = True
            along.append(nodes)
        for i, x in enumerate(paths):
            seq = (x["source"],) + tuple(h["edge"] for h in x["hops"])
            same = 0                                      # leading IDs it shares with the last path
            while full and same < len(seq) and same < len(last) and seq[same] == last[same]:
                same += 1
            avoid, at, total = {x["source"]}, x["source"], 0
            for j, tree in enumerate(along[i]):
                if j:
                    h = x["hops"][j - 1]
                    at = h["to"]
                    avoid.add(at)
                    total += price(h["edge"], h["relation"])
                if tree[2]:
                    continue
                tree[2] = True
                where = f"the prefix of path {i} by {j} hops"
                if at in targets and not tree[1]:
                    self.bad(f"certificate: {where} ends at the target {at!r}, a path before path {i}, unlisted")

                def side(eid: str, j: int = j, same: int = same, seq: tuple = seq) -> int:
                    """On a tie of cost and hops, whether a walk leaving the prefix by ``eid`` comes after the last
                    path: as its IDs compare."""
                    if not full:
                        return 0
                    if j + 1 <= same:                     # the last path's prefix: the next edge decides
                        return int(len(last) == j + 1 or eid > last[j + 1])
                    return int(same == len(last) or seq[same] > last[same])
                starts = [((total + price(eid, rel), j + 1, side(eid)), v, eid)
                          for eid, v, rel in self.hops_from(at, allowed) if v not in avoid and eid not in tree[0]]
                explore(where, explored.pop((i, j), {}), starts, avoid)
        for i, j in sorted(explored, key=str):
            self.bad(f"certificate: the entry {(i, j)} is not where a prefix of the listed paths first appears")

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
        and the listed intervals, the residual and the certified prefix must be exactly those, and no node with a
        positive estimate left out may outrank a listed one (or fit within ``top``). Without a certificate only the
        records' nodes are checked."""
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
        worst = max(((-estimate.get(u, Fraction(0)), u) for u in listed), default=None)
        for u in sorted(estimate, key=str):               # the top nodes by lower bound, ties by ID: none outranks them
            if estimate[u] > 0 and u not in listed and (len(listed) < request["top"] or (-estimate[u], u) < worst):
                self.bad(f"{u!r} has the lower bound {estimate[u]} and is not ranked, but outranks a ranked node "
                         f"or fits within top")
                break
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
        """(edge, the node whose hop it is, its relation) for every hop in scope that leads to ``u``. The reversed
        scope is built once per scope, not per node (a scope of many relations made that quadratic)."""
        if self._flipped is None or self._flipped[0] is not allowed:
            self._flipped = (allowed, {(r, "reverse" if d == "forward" else "forward") for r, d in allowed})
        return self.hops_from(u, self._flipped[1])

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
        are pairs, so no matching has more. With costs: the stated cost the exact sum of the pairs' hops; and the
        potentials, when given, leave no arc of the residual network (source to an unpaired left node, a paired left
        node back to the source, a candidate hop not used by a pair, a pair's hop backwards at minus its cost, an
        unpaired partner to the sink, the sink back to a paired one) costing less than nothing once reduced, so no
        matching of the same size is cheaper."""
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
        costs = request.get("costs")
        kind, table = next(iter(costs.items())) if costs else (None, None)
        rational = table is not None and any(isinstance(x, float) for x in table.values())

        def price(eid: str) -> Any:
            x = table.get(self.edge_of[eid][2] if kind == "by_relation" else eid) if eid in self.edge_of else None
            if x is None:
                self.bad(f"the hop {eid} in scope has no cost in the request")
                return 0
            return Fraction(x) if rational else x
        if table is not None:
            total = sum((price(x["edge"]) for x in answer.get("pairs") or ()), Fraction(0) if rational else 0)
            stated = Fraction(answer["cost_exact"]) if rational and isinstance(answer.get("cost_exact"), str) \
                else answer.get("cost")
            if stated != total:
                self.bad(f"the pairs cost {total}, but the answer says {stated!r}")
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
        if table is None:
            return
        number = (lambda x: Fraction(x)) if rational else (lambda x: x)
        value = {e["node"]: number(e["value"]) for e in cert.get("potentials") or ()}
        source, sink = number(cert["source"]), number(cert["sink"])
        paired = {x["left"]: x["edge"] for x in answer.get("pairs") or ()}
        partners = {x["right"] for x in answer.get("pairs") or ()}
        candidates = {u: [(eid, v) for eid, v, _ in self.hops_from(u, allowed) if v not in left]
                      for u in sorted(left, key=str)}
        nodes = set(left) | {v for hops in candidates.values() for _, v in hops}
        missing = sorted(nodes - set(value), key=str)
        if missing:
            self.bad(f"certificate: no potential for {missing[:5]}")
            return

        def arc(where: str, reduced: Any) -> None:
            if reduced < 0:
                self.bad(f"certificate: the residual arc {where} costs {reduced} once reduced")
        for u, hops in candidates.items():
            arc(f"source -> {u!r}" if u not in paired else f"{u!r} -> source",
                source - value[u] if u not in paired else value[u] - source)
            for eid, v in hops:
                if paired.get(u) == eid:
                    arc(f"{v!r} -> {u!r} (by {eid})", -price(eid) + value[v] - value[u])
                else:
                    arc(f"{u!r} -> {v!r} (by {eid})", price(eid) + value[u] - value[v])
        for v in sorted(nodes - set(left), key=str):
            arc(f"{v!r} -> sink" if v not in partners else f"sink -> {v!r}",
                value[v] - sink if v not in partners else sink - value[v])


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

    def flux_modes(self, request: Mapping, answer: Mapping, status: str) -> None:
        """Each mode against the records: the stoichiometric matrix rebuilt from them (as for incidence), the vector's
        reactions columns of it, its values non-zero integers with no common factor, an irreversible reaction's
        non-negative, and S v = 0 exactly; the modes distinct and in the order of (support size, reactions and values
        by ID). With the certificate, each mode's minor: |J| - 1 species and |J| - 1 of its reactions whose
        determinant is non-zero, so its support's columns have rank |J| - 1 and the mode is elementary. That the list
        holds every mode is not checked here (the enumeration; the oracle tests)."""
        allowed = {(e["relation"], e["direction"]) for e in request["scope"]}
        rows, cols = self.chosen(request["rows"]), self.chosen(request["columns"])
        if answer.get("species") != len(rows) or answer.get("reactions") != len(cols):
            self.bad(f"the network has {len(rows)} species and {len(cols)} reactions")
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
        by_col: dict = {}
        for (u, v), a in matrix.items():
            by_col.setdefault(v, []).append((u, a))
        backwards = set(request["reversible"])
        keys = []
        for n, mode in enumerate(answer.get("modes") or ()):
            where = f"mode {n}"
            vec = [(e.get("reaction"), e.get("value")) for e in mode.get("vector") or ()]
            if not vec or any(r not in cols or not _is_int(x) or x == 0 for r, x in vec) \
                    or len({r for r, _ in vec}) != len(vec) or mode.get("support") != len(vec):
                self.bad(f"{where}: its reactions are distinct columns, each with a non-zero integer, counted")
                continue
            g = 0
            for _, x in vec:
                g = gcd(g, x)
            if g != 1:
                self.bad(f"{where}: its values share the factor {g}")
            if any(x < 0 and r not in backwards for r, x in vec):
                self.bad(f"{where}: an irreversible reaction runs backwards")
            balance: dict = {}
            for r, x in vec:
                for u, a in by_col.get(r, ()):
                    balance[u] = balance.get(u, Fraction(0)) + a * x
            if any(balance.values()):
                self.bad(f"{where}: S v is not zero")
                continue
            keys.append((len(vec), sorted(vec)))
            cert = self.proof(mode)
            if cert is None:
                continue
            support = {r for r, _ in vec}
            mr, mc = list(cert.get("rows") or ()), list(cert.get("columns") or ())
            if len(mr) != len(vec) - 1 or len(mc) != len(vec) - 1 or len(set(mr)) != len(mr) \
                    or len(set(mc)) != len(mc) or not set(mr) <= rows or not set(mc) <= support:
                self.bad(f"{where}: the minor takes |J| - 1 distinct species and |J| - 1 of the mode's reactions")
                continue
            if not _nonsingular([[matrix.get((u, v), Fraction(0)) for v in mc] for u in mr]):
                self.bad(f"{where}: the minor's determinant is zero: it does not prove the mode elementary")
        if any(a >= b for a, b in zip(keys, keys[1:])):
            self.bad("the modes are distinct and in the order of (support size, reactions and values by ID)")

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
