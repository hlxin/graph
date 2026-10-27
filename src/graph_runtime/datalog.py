"""Positive Datalog over a projection: rules derive facts from the records, and every fact comes with its derivation.

A program is a set of rules ``head :- body``, each atom a predicate over terms (a variable ``"?x"`` or a constant). It
is positive, function-free and range-restricted, so its meaning is its least model: the smallest set of facts that
holds the inputs and is closed under every rule (Abiteboul, Hull and Vianu, 1995). The inputs are the projection's
records: ``node(N)`` for every node, ``type(N, T)`` for every type a node has (with the hierarchy: a node of a type
has its ancestors too), ``edge(E, S, T, R)`` for every edge from S to T, for its relation and every relation above it.

Evaluation is semi-naive (Bancilhon, 1986), predicate by predicate in the order of their dependencies (the strongly
connected components of the rules' predicate graph, dependencies first): within a component, each round fires every
rule once for each body atom of the component, that atom over the facts the last round found and the others over
every fact found so far, so that each derivation uses a new fact; the facts a round finds join the model when it
ends. A rule's atoms are joined in a fixed order: the new-fact atom first, then always the atom with the most
arguments bound, the earliest on ties; each looks its candidates up in an index on its bound arguments. Every fact
read, input or derived, is one expansion; so is every input fact loaded.

Each derived fact keeps one derivation, the first found in that order: the rule and, for each body atom, the fact it
matched. Its rank is the round that found it, and every premise has a lower rank, so derivations are well founded and
a checker confirms one in the size of the proof. Facts, rounds and derivations are the same on every run (new facts
are taken in sorted order, never in hash order).
"""

from __future__ import annotations

import heapq
from types import SimpleNamespace
from typing import Any

INPUTS = ("node", "type", "edge")


class _Stop(Exception):
    """A limit, a deadline or a cancellation stopped the evaluation."""


def _term(value: Any) -> tuple[str, str]:
    """('v', name) for a variable, ('c', text) for a constant."""
    if isinstance(value, str):
        return ("v", value) if value.startswith("?") else ("c", value)
    return ("c", value["const"])


class _Relation:
    """A predicate's facts in the order found, each with its derivation (None for an input), and indexes on bound
    arguments, kept up to date as facts join. Building an index reads every fact once (one expansion each), and
    keeping it reads each new fact once more."""

    __slots__ = ("facts", "indexes")

    def __init__(self) -> None:
        self.facts: dict[tuple, Any] = {}
        self.indexes: dict[tuple, dict] = {}

    def add(self, fact: tuple, how: Any) -> None:
        self.facts[fact] = how
        for positions, index in self.indexes.items():
            index.setdefault(tuple(fact[k] for k in positions), []).append(fact)

    def lookup(self, positions: tuple, key: tuple, ctl: Any) -> Any:
        if not positions:
            return self.facts
        index = self.indexes.get(positions)
        if index is None:
            _charge(ctl, len(self.facts))
            index = self.indexes[positions] = {}
            for fact in self.facts:
                index.setdefault(tuple(fact[k] for k in positions), []).append(fact)
        return index.get(key, ())


def _inputs(p: Any, used: set, ctl: Any) -> dict[str, _Relation]:
    """The input predicates the program uses, from the records, each fact one expansion."""
    out = {}
    if "node" in used:
        rel = out["node"] = _Relation()
        _charge(ctl, len(p._node_ids))
        for nid in p._node_ids:
            rel.add((nid,), None)
    if "type" in used:
        rel = out["type"] = _Relation()
        types = p._node_types
        for u, nid in enumerate(p._node_ids):
            mask = p._node_mask[u]
            _charge(ctl, bin(mask).count("1"))                 # the node's types, counted before they are read
            while mask:                                        # only the bits that are set, in ascending order
                low = mask & -mask
                rel.add((nid, types[low.bit_length() - 1]), None)
                mask ^= low
    if "edge" in used:
        rel = out["edge"] = _Relation()
        names, parents, at = p._relations, p._relation_parents, p._rel_index
        above: dict[int, list[str]] = {}                       # a relation edges use -> it and every one above it

        def up(r: int) -> list[str]:
            found, todo = {names[r]}, [names[r]]
            while todo:
                links = parents.get(todo.pop(), ())
                _charge(ctl, len(links))                       # each parent link, before it is followed
                for q in links:
                    if q not in found:
                        found.add(q)
                        todo.append(q)
            return sorted(found, key=at.__getitem__)
        ids, src, dst, erel, nodes = p._edge_ids, p._edge_src, p._edge_dst, p._edge_rel, p._node_ids
        for ei in range(len(ids)):
            _charge(ctl, 1)                                    # the edge, before its relations are resolved
            rs = above.get(erel[ei])
            if rs is None:
                rs = above[erel[ei]] = up(erel[ei])
            _charge(ctl, len(rs) - 1)
            for r in rs:
                rel.add((ids[ei], nodes[src[ei]], nodes[dst[ei]], r), None)
    return out


def _charge(ctl: Any, k: int) -> None:
    why = ctl.take(k)
    if why:
        raise _Stop(why)


def _strata(rules: list) -> list[list[str]]:
    """The derived predicates in strongly connected components of the predicate graph, dependencies first (Tarjan's
    algorithm, iteratively, over predicates in name order)."""
    preds = sorted({r.head[0] for r in rules})
    uses = {p: set() for p in preds}
    for r in rules:
        uses[r.head[0]].update(a[0] for a in r.body if a[0] in uses)
    index, low, on, stack, order, counter = {}, {}, set(), [], [], [0]
    for root in preds:
        if root in index:
            continue
        work = [(root, iter(sorted(uses[root])))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            w = next(it, None)
            if w is not None:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(uses[w]))))
                elif w in on:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                order.append(sorted(comp))
    return order                                               # Tarjan emits a component after those it uses


def _plan(rule: SimpleNamespace, first: int | None) -> list[tuple]:
    """The join order of a rule's body, starting from atom ``first`` (or none): each step (atom, the positions bound
    before it with how to read them, the positions it binds with their variables, the positions that repeat a
    variable bound earlier in the same atom)."""
    bound: set = set()
    body = rule.body
    score = [sum(1 for kind, _ in atom[1] if kind == "c") for atom in body]     # arguments bound so far, per atom
    holding: dict = {}                                       # a variable -> the atoms it occurs in, once per place
    for a, atom in enumerate(body):
        for kind, x in atom[1]:
            if kind == "v":
                holding.setdefault(x, []).append(a)
    heap = [(-score[a], a) for a in range(len(body))]        # the most bound first, the earliest on ties (lazily)
    heapq.heapify(heap)
    left = set(range(len(body)))
    steps = []
    while left:
        if first is not None and first in left:
            j = first
        else:
            while True:
                neg, j = heapq.heappop(heap)
                if j in left and -neg == score[j]:
                    break
        left.discard(j)
        keyed, binds, repeats = [], [], []
        mine: dict = {}
        for k, (kind, x) in enumerate(rule.body[j][1]):
            if kind == "c" or x in bound:
                keyed.append((k, kind, x))
            elif x in mine:
                repeats.append((k, mine[x]))
            else:
                mine[x] = k
                binds.append((k, x))
        for x in mine:                                       # newly bound: every other place it occurs gains one
            for a in holding[x]:
                if a in left:
                    score[a] += 1
                    heapq.heappush(heap, (-score[a], a))
        bound.update(mine)
        steps.append((j, tuple(k for k, _, _ in keyed), tuple(keyed), tuple(binds), tuple(repeats)))
    return steps


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """The least model, stratum by stratum, round by round. An interruption keeps the rounds completed: every fact in
    them derived, never the whole model."""
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, relations=None, rounds=0, complete=False,
                         keep=plan.derivations or plan.certificate)        # premises are kept only when shown
    rules = plan.rules
    used = {a[0] for r in rules for a in r.body}
    try:
        relations = _inputs(p, used, ctl)
    except _Stop as stop:
        st.interruption = str(stop)
        return st
    for r in rules:
        relations.setdefault(r.head[0], _Relation())
    st.relations = relations
    by_head: dict[str, list] = {}
    for r in rules:
        by_head.setdefault(r.head[0], []).append(r)
    try:
        for stratum in _strata(rules):
            fired = [r for pred in stratum for r in by_head[pred]]
            fired.sort(key=lambda r: r.index)
            new = _round(fired, None, relations, stratum, ctl, st)
            while any(new.values()):
                delta = {pred: sorted(new[pred]) for pred in stratum if new[pred]}    # predicates in name order
                _charge(ctl, sum(len(facts) * len(relations[pred].indexes) for pred, facts in delta.items()))
                st.rounds += 1                                 # the whole round joins, or none of it
                for pred, facts in delta.items():
                    rel = relations[pred]
                    for fact in facts:
                        rel.add(fact, new[pred][fact] + (st.rounds,))        # (rule, premises, rank)
                    st.discovered += len(facts)
                new = _round(fired, delta, relations, stratum, ctl, st)
    except _Stop as stop:
        st.interruption = str(stop)
        return st
    st.complete = True
    return st


def _round(rules: list, delta: dict | None, relations: dict, inside: list, ctl: Any, st: SimpleNamespace) -> dict:
    """One round: every rule fired once without new facts (the first round of a stratum, ``delta`` None), or once per
    body atom of the stratum over the last round's new facts. Returns the facts found, each with its first
    derivation (rule, the matched fact of every body atom), in the order found."""
    found: dict[str, dict] = {pred: {} for pred in inside}
    for rule in rules:
        if delta is None:
            variants = [None]
        else:
            variants = [j for j, a in enumerate(rule.body) if a[0] in delta]
        for first in variants:
            _fire(rule, first, delta, relations, found, ctl, st)
    return found


def _fire(rule: SimpleNamespace, first: int | None, delta: dict | None, relations: dict, found: dict, ctl: Any,
          st: SimpleNamespace) -> None:
    """Every binding of a rule's body, atom ``first`` over the new facts and the others over every fact so far, by an
    explicit stack (as deep as the body is long); a head neither known nor found yet joins ``found``."""
    steps = rule.plans.get(first)
    if steps is None:                                          # a join order, kept: one expansion per body atom
        _charge(ctl, len(rule.body))
        steps = rule.plans[first] = _plan(rule, first)
    head_pred, head_terms = rule.head
    known, mine = relations[head_pred].facts, found[head_pred]
    keep = st.keep
    val: dict = {}
    matched: list = [None] * len(rule.body)

    def candidates(s: int) -> Any:
        j, positions, keyed, _, _ = steps[s]
        key = tuple(x if kind == "c" else val[x] for _, kind, x in keyed)
        if j == first:                                       # the new facts, every one read
            new = delta[rule.body[j][0]]
            _charge(ctl, len(new))
            st.examined += len(new)
            return [f for f in new if all(f[k] == v for k, v in zip(positions, key))]
        facts = relations[rule.body[j][0]].lookup(positions, key, ctl)
        _charge(ctl, len(facts))
        st.examined += len(facts)
        return list(facts)
    if not steps:                                              # a fact the program states
        head = tuple(x for _, x in head_terms)
        if head not in known and head not in mine:
            mine[head] = (rule.index, ())
        return
    frames = [[0, candidates(0), 0]]
    while frames:
        frame = frames[-1]
        s, facts, k = frame
        if k == len(facts):
            frames.pop()
            continue
        frame[2] = k + 1
        j, _, _, binds, repeats = steps[s]
        fact = facts[k]
        if any(fact[a] != fact[b] for a, b in repeats):
            continue
        for pos, x in binds:
            val[x] = fact[pos]
        matched[j] = fact
        if s + 1 < len(steps):
            frames.append([s + 1, candidates(s + 1), 0])
            continue
        head = tuple(x if kind == "c" else val[x] for kind, x in head_terms)
        if head not in known and head not in mine:
            if keep:                                           # a derivation kept: one expansion per premise
                _charge(ctl, len(matched))
                mine[head] = (rule.index, tuple(matched))
            else:
                mine[head] = (rule.index, None)


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    complete = st.interruption is None
    empty = {"rows": [], "count": 0, "total": None, "exists": None, "facts": [], "model": None, "certificate": None}
    if st.relations is None:
        return empty, []
    pred, terms = plan.query
    first: dict = {}
    matches = []
    for fact in st.relations[pred].facts:                      # the facts of completed rounds
        ok = True
        for k, (kind, x) in enumerate(terms):
            if kind == "c":
                ok = fact[k] == x
            else:
                ok = first.setdefault(x, k) == k or fact[first[x]] == fact[k]
            if not ok:
                break
        if ok:
            matches.append(fact)
    matches.sort()
    total = len(matches) if complete else None
    cap = plan.max_results if plan.answer == "rows" else 0
    listed = matches[:cap]
    if complete and plan.answer == "rows" and len(matches) > cap:
        st.interruption = "output_limit"                  # the total stays exact; the certificate goes with it
    whole = plan.certificate and st.interruption is None
    chosen: list = []
    if plan.derivations or whole:
        if whole:
            chosen = [(q, f) for q, rel in st.relations.items() if q not in INPUTS for f in rel.facts]
        else:
            seen: set = set()
            todo = [(pred, f) for f in listed]
            while todo:
                item = todo.pop()
                if item in seen:
                    continue
                seen.add(item)
                how = st.relations[item[0]].facts[item[1]]
                rule = plan.rules[how[0]]
                for (bp, _), premise in zip(rule.body, how[1]):
                    if bp not in INPUTS:
                        todo.append((bp, premise))
            chosen = list(seen)
    rank = {item: st.relations[item[0]].facts[item[1]][2] for item in chosen}
    chosen.sort(key=lambda item: (rank[item], item))           # by rank, then predicate and arguments: premises first
    at = {item: k for k, item in enumerate(chosen)}
    facts = []
    for q, f in chosen:
        how = st.relations[q].facts[f]
        rule = plan.rules[how[0]]
        premises = []
        for (bp, _), premise in zip(rule.body, how[1]):
            premises.append({"input": bp, "args": list(premise)} if bp in INPUTS else {"fact": at[(bp, premise)]})
        facts.append({"predicate": q, "args": list(f), "rule": rule.id, "rank": rank[(q, f)], "premises": premises})
    rows = [{"values": list(f), "fact": at[(pred, f)] if plan.derivations else None} for f in listed]
    exists = (total > 0) if total is not None else (True if matches else None)
    model = {"facts": sum(len(r.facts) for q, r in st.relations.items() if q not in INPUTS), "rounds": st.rounds} \
        if complete else None
    cert = {"kind": "least_model"} if whole else None
    return ({"rows": rows, "count": len(rows), "total": total, "exists": exists, "facts": facts, "model": model,
             "certificate": cert}, [DATALOG_NOTE] if rows else [])


DATALOG_NOTE = ("Each row is a fact of the least model with its derivation: a rule, and for each body atom an input "
                "record or an earlier derived fact. Without the certificate, that no other row exists is not proven.")
