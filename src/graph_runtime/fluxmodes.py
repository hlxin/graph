"""Elementary flux modes: the minimal pathways of a reaction network at steady state.

The stoichiometric matrix S is ``incidence``'s: a row for each species (row node), a column for each reaction (column
node), S[i][j] the sum of the coefficients of the edges in scope joining them, either way (consumption negative,
production positive). A flux vector v is feasible when S v = 0 and every irreversible reaction carries v_j >= 0; a
feasible v != 0 is an elementary flux mode when no feasible vector has a smaller support (Schuster and Hilgetag, 1994).
Every steady state is a non-negative combination of them: they are a network's indivisible routes.

Enumeration is exact double description in its nullspace form (Wagner, 2004; Gagneur and Klamt, 2004; Terzer and
Stelling, 2008). Each reversible reaction is split into a forward and a reverse column (the reverse one negated), so
every flux is non-negative; the cone {v >= 0 : S v = 0} of the split network is pointed, and its extreme rays are the
elementary modes, besides the trivial cycle of a reaction's two halves. Its kernel's canonical basis (one vector per
free column: 1 there, 0 at the others) spans the cone of vectors whose free coordinates are non-negative; then each
other coordinate in turn (the one with the fewest positive-negative pairs, the lowest index on ties) is made
non-negative: the rays positive or zero there stay, and every pair of a positive ray p and a negative ray n that are
adjacent (no other ray is zero wherever both are, on the coordinates made non-negative so far) gives the ray
(-n_j) p + p_j n, zero there. At the end each ray, its two halves merged, is a mode, scaled to a primitive integer
vector. Each pair tried is one expansion, and so is each ray its adjacency test reads; the matrix and its kernel cost
as ``incidence``'s. The number of modes can grow exponentially with the network: the budget bounds the enumeration,
and an interrupted one lists none.

Each mode carries a proof that it is elementary: it is feasible (S v = 0 exactly, irreversible reactions
non-negative), and the columns of its support J have rank |J| - 1, shown by a nonsingular (|J| - 1)-minor of S_J, so
no feasible vector has a support strictly inside J. That the list holds every mode is the enumeration's, not
certified (a full transcript of the double description would be as large as its work).
"""

from __future__ import annotations

from fractions import Fraction
from types import SimpleNamespace
from typing import Any

from .algebra import _Stop, _forward, _integers, _kernel, _primitive


def _charge(ctl: Any, k: int) -> None:
    why = ctl.take(k)
    if why:
        raise _Stop(why)


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    from .engine import _sides
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, modes=None, total=None)
    rows, cols = plan.rows, plan.columns
    col_index = {c: j for j, c in enumerate(cols)}
    either = [f or r for f, r in zip(plan.fwd, plan.rev)]
    sides, rel = _sides(p, either, either), p._edge_rel
    try:
        matrix: list[dict[int, Fraction]] = []
        for u in rows:                                         # one expansion per species read
            _charge(ctl, 1)
            row: dict[int, Fraction] = {}
            seen = set()
            for lists, base, marks, ends, _ in sides:
                hs = lists[u]
                if hs is None:
                    hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
                st.examined += len(hs)
                for ei in hs:
                    j = col_index.get(ends[ei])
                    if j is None or ei in seen:
                        continue
                    seen.add(ei)
                    row[j] = row.get(j, 0) + plan.coefficient(ei)
            matrix.append({j: x for j, x in row.items() if x})
        st.discovered = len(rows) + len(cols)
        halves: list[tuple[int, int]] = []                     # split column -> (reaction, +1 forward or -1 reverse)
        at: list[list[tuple[int, int]]] = []                   # reaction -> its split columns, with their signs
        for j in range(len(cols)):
            at.append([(len(halves), 1)])
            halves.append((j, 1))
            if plan.reversible[j]:
                at[j].append((len(halves), -1))
                halves.append((j, -1))
        split = [{k: x * sign for j, x in row.items() for k, sign in at[j]} for row in matrix]   # by entries only
        n = len(halves)
        echelon = _forward([_integers(r) for r in split], n, ctl)
        basis = _kernel(*echelon, n, ctl)
        modes = _describe(basis, set(range(n)) - set(echelon[0]), halves, n, ctl)
        ids = p._node_ids
        named = sorted(((len(vec), sorted((ids[cols[j]], x) for j, x in vec.items()), vec) for vec in modes),
                       key=lambda m: (m[0], m[1]))
        st.total = len(named)
        listed = named[:plan.max_results]
        st.modes = []
        by_col: dict[int, list[tuple[int, Fraction]]] = {}
        if plan.certificate:
            for i, row in enumerate(matrix):
                for j, x in row.items():
                    by_col.setdefault(j, []).append((i, x))
        for size, entries, vec in listed:                      # the proofs of the modes listed, charged
            proof = _minor(by_col, list(vec), ctl) if plan.certificate else None
            st.modes.append((size, entries, proof))
    except _Stop as stop:
        st.interruption, st.modes = stop.why, None
    return st


def _support(vec: dict[int, int]) -> int:
    bits = 0
    for k in vec:
        bits |= 1 << k
    return bits


def _describe(basis: list[dict[int, int]], free: set[int], halves: list[tuple[int, int]], n: int,
              ctl: Any) -> list[dict[int, int]]:
    """The extreme rays of {v >= 0 : S v = 0} by double description from the kernel's canonical basis (non-negative
    on the free columns), then merged into modes of the reactions (a ray over both halves of one reaction, the trivial
    cycle, dropped)."""
    words = 1 + n // 64                                        # a ray's support, as a bitset of the split columns
    _charge(ctl, len(basis) * words)                           # each starting ray, before it is built
    rays = [(vec, _support(vec)) for vec in basis]
    done = 0
    for k in free:
        done |= 1 << k
    todo = [k for k in range(n) if k not in free]
    while todo and rays:                                       # no ray left: no mode, whatever remains
        _charge(ctl, len(rays) * len(todo))                    # choosing the coordinate reads every ray once per one

        def pairs(k: int) -> tuple:
            pos = sum(1 for v, _ in rays if v.get(k, 0) > 0)
            neg = sum(1 for v, _ in rays if v.get(k, 0) < 0)
            return (pos * neg, k)
        k = min(todo, key=pairs)
        todo.remove(k)
        pos = [i for i, (v, _) in enumerate(rays) if v.get(k, 0) > 0]
        neg = [i for i, (v, _) in enumerate(rays) if v.get(k, 0) < 0]
        kept = [(v, s) for v, s in rays if v.get(k, 0) >= 0]
        for ip in pos:
            vp, sp = rays[ip]
            for jn in neg:
                vn, sn = rays[jn]
                _charge(ctl, 1 + len(rays))                    # the pair, and the adjacency test over every ray
                union = (sp | sn) & done
                adjacent = all((sr & done) | union != union for i, (_, sr) in enumerate(rays) if i != ip and i != jn)
                if not adjacent:                               # another ray is zero wherever both are
                    continue
                _charge(ctl, words)                            # the new ray
                a, b = -vn[k], vp[k]
                new = {}
                for c in set(vp) | set(vn):
                    x = a * vp.get(c, 0) + b * vn.get(c, 0)
                    if x:
                        new[c] = x
                new = _primitive(new)
                kept.append((new, _support(new)))
        rays = kept
        done |= 1 << k
    modes = []
    seen = set()
    _charge(ctl, len(rays))                                    # each ray merged into a mode
    for vec, _ in rays:
        merged: dict[int, int] = {}
        both = False
        for k, x in vec.items():
            j, sign = halves[k]
            if j in merged:
                both = True
            merged[j] = merged.get(j, 0) + sign * x
        if both:                                               # a reaction's two halves: the trivial cycle
            continue
        merged = _primitive({j: x for j, x in merged.items() if x})
        key = tuple(sorted(merged.items()))
        if merged and key not in seen:
            seen.add(key)
            modes.append(merged)
    return modes


def _minor(by_col: dict[int, list[tuple[int, Fraction]]], support: list[int], ctl: Any) -> tuple[list[int], list[int]]:
    """Rows and columns of a nonsingular (|J| - 1)-minor of S restricted to the support J, read from the support's
    columns (one expansion, plus one per entry read)."""
    _charge(ctl, 1 + sum(len(by_col.get(j, ())) for j in support))
    rows: dict[int, dict[int, Fraction]] = {}
    for j in support:
        for i, x in by_col.get(j, ()):
            rows.setdefault(i, {})[j] = x
    which = sorted(rows)
    sub = [_integers(rows[i]) for i in which]
    order = sorted(support)
    local = {j: k for k, j in enumerate(order)}
    pivots, used, _ = _forward([{local[j]: x for j, x in r.items()} for r in sub], len(order), ctl)
    return [which[i] for i in used], [order[c] for c in pivots]


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    ids = p._node_ids
    rows, cols = plan.rows, plan.columns
    base = {"species": len(rows), "reactions": len(cols)}
    if st.modes is None:
        return {**base, "modes": [], "count": 0, "total": None}, []
    if st.total > plan.max_results:
        st.interruption = "output_limit"
    modes = []
    for size, entries, proof in st.modes:
        mode = {"vector": [{"reaction": r, "value": x} for r, x in entries], "support": size, "certificate": None}
        if proof is not None:
            used, pivots = proof
            mode["certificate"] = {"kind": "support_rank", "rows": [ids[rows[i]] for i in used],
                                   "columns": [ids[cols[j]] for j in pivots]}
        modes.append(mode)
    return {**base, "modes": modes, "count": len(modes), "total": st.total}, [MODES_NOTE] if modes else []


MODES_NOTE = ("Each mode is feasible (S v = 0 exactly, irreversible reactions non-negative) and elementary, proven by "
              "a nonsingular minor showing its support's columns have rank one less than its size; that the list "
              "holds every mode is the enumeration's, not a certificate.")
