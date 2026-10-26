"""Exact linear algebra of an incidence structure: the rank of the matrix whose entries are the coefficients of the
edges joining row nodes to column nodes, and bases of its two kernels.

The matrix N has a row for each row node and a column for each column node (both in ascending ID); N[i][j] adds the
coefficients of the edges in scope that join row i and column j, whichever way they point. For a reaction network's
stoichiometric matrix (species by reactions, a reaction's consumption negative and its production positive), the rank
is the number of independent reactions (with the numbers of complexes and of linkage classes it gives the deficiency
of Feinberg's theory, 1987); the left kernel holds the conservation laws, weights of the species that no reaction
changes (mass, atoms, sites); the right kernel the reaction combinations that change nothing (cycles, steady fluxes).
For a Petri net's incidence matrix, the left kernel holds the place invariants and the right kernel the transition
invariants. Arithmetic is exact: coefficients are integers, or floats read as their exact binary values.

Each basis is canonical: the kernel's reduced row echelon basis (one vector per free coordinate, 1 there and 0 at the
other free coordinates), each vector scaled by a positive factor to a primitive integer vector.

The proof of the rank r: an r by r minor of N with a nonzero determinant shows rank at least r, and independent
vectors of a kernel (each nonzero at a coordinate where the others are zero), n - r of the right one or m - r of the
left, show rank at most r; together they show the rank, and that the listed kernels are complete. A checker
multiplies every listed vector by N and shows the minor nonsingular, from the records: by elimination modulo a large
prime (a determinant nonzero modulo p is nonzero), or, should that fail, exactly.
"""

from __future__ import annotations

from fractions import Fraction
from math import gcd
from types import SimpleNamespace
from typing import Any


class _Stop(Exception):
    def __init__(self, why: str):
        self.why = why


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """The matrix, read from the edges in scope at the row nodes (each row one expansion), then its row echelon form by
    exact fraction-free elimination over the integers (each row scaled by the least common denominator of its entries,
    which changes neither the rank nor the right kernel; the transpose's rows likewise for the left kernel): the rank and a nonsingular minor. A kernel comes from back
    elimination of the echelon form of the matrix (right) or of its transpose (left); each pivot, forward or back,
    is one expansion. Without the kernels asked for, the certificate takes the smaller one. An interrupted reduction
    claims nothing."""
    from .engine import _sides
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, rank=None, minor=None, left=None, right=None,
                         proof=None)
    rows, cols = plan.rows, plan.columns
    col_index = {c: j for j, c in enumerate(cols)}
    either = [f or r for f, r in zip(plan.fwd, plan.rev)]
    sides, rel = _sides(p, either, either), p._edge_rel
    matrix: list[dict[int, Fraction]] = []
    try:
        for u in rows:
            why = ctl.stop()
            if why:
                raise _Stop(why)
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
        echelon = _forward([_integers(r) for r in matrix], len(cols), ctl)   # scaling a row keeps the right kernel
        pivots, used = echelon[0], echelon[1]
        st.rank, st.minor = len(pivots), (used, pivots)
        transpose: list[dict[int, Fraction]] = [{} for _ in cols]
        for i, row in enumerate(matrix):
            for j, x in row.items():
                transpose[j][i] = x
        want_left, want_right = plan.left, plan.right
        if plan.certificate and not want_left and not want_right:     # the smaller kernel bounds the rank
            st.proof = "left" if len(rows) <= len(cols) else "right"
            want_left, want_right = st.proof == "left", st.proof == "right"
        if want_right:
            st.right = _kernel(*echelon, len(cols), ctl)
        if want_left:
            st.left = _kernel(*_forward([_integers(r) for r in transpose], len(rows), ctl), len(rows), ctl)
    except _Stop as stop:
        st.interruption, st.rank = stop.why, None
    return st


def _integers(row: dict[int, Any]) -> dict[int, int]:
    """A row of rationals as integers: scaled by the least common denominator, then divided by the gcd."""
    den = 1
    for x in row.values():
        x = Fraction(x)
        den = den * x.denominator // gcd(den, x.denominator)
    ints = {j: int(Fraction(x) * den) for j, x in row.items()}
    return _primitive(ints)


def _primitive(row: dict[int, int]) -> dict[int, int]:
    g = 0
    for v in row.values():
        g = gcd(g, v)
        if g == 1:
            return row
    return {j: v // g for j, v in row.items()} if g > 1 else row


def _forward(rows: list[dict[int, int]], n: int, ctl: Any) -> tuple[list[int], list[int], list[dict[int, int]]]:
    """A row echelon form by exact fraction-free elimination over the integers, column by column in ascending order:
    at each column the sparsest open row with a nonzero entry there (the lowest index on a tie) becomes its pivot row,
    and every other open row with an entry there becomes ``a * row - b * pivot`` (a, b its two entries over their gcd),
    divided by its own gcd. A pivot row then has nothing before its pivot column (every earlier column either holds an
    earlier pivot, cleared from it, or no open row had an entry there). Returns the pivot columns, the original rows
    that became pivot rows (independent on the pivot columns: each is its original row plus multiples of earlier
    pivot rows), and the pivot rows. Each pivot is one expansion."""
    work = [dict(r) for r in rows]
    holding: dict[int, set[int]] = {}
    for i, r in enumerate(work):
        for c in r:
            holding.setdefault(c, set()).add(i)
    open_rows = set(range(len(work)))
    pivots, used = [], []
    for c in range(n):
        cand = [i for i in holding.get(c, ()) if i in open_rows]
        if not cand:
            continue
        why = ctl.stop()
        if why:
            raise _Stop(why)
        i = min(cand, key=lambda k: (len(work[k]), k))
        open_rows.discard(i)
        prow = work[i]
        a = prow[c]
        for k in cand:
            if k == i:
                continue
            row = work[k]
            b = row[c]
            g = gcd(a, b)
            fa, fb = a // g, b // g
            new = {col: v * fa for col, v in row.items()}
            for col, v in prow.items():
                x = new.get(col, 0) - fb * v
                if x:
                    new[col] = x
                else:
                    new.pop(col, None)
            new = _primitive(new)
            for col in row:
                if col not in new:
                    holding[col].discard(k)
            for col in new:
                if col not in row:
                    holding.setdefault(col, set()).add(k)
            work[k] = new
        pivots.append(c)
        used.append(i)
    return pivots, used, [work[i] for i in used]


def _kernel(pivots: list[int], used: list[int], echelon: list[dict[int, int]], n: int,
            ctl: Any) -> list[dict[int, int]]:
    """The kernel's canonical basis from a row echelon form: back elimination (each pivot row cleared, fraction-free,
    of every later pivot column, from the last pivot up) leaves pivot row k with entries only at its pivot and at free
    columns; for each free column f, the vector with 1 at f, 0 at the other free columns and -row_k[f] / row_k[p_k]
    at each pivot column p_k (the reduced row echelon basis), scaled by a positive factor to a primitive integer
    vector. Each back-eliminated pivot is one expansion."""
    reduced = [dict(r) for r in echelon]
    position = {piv: k for k, piv in enumerate(pivots)}
    for k in range(len(pivots) - 1, -1, -1):
        why = ctl.stop()
        if why:
            raise _Stop(why)
        row = reduced[k]
        for j in sorted((c for c in row if c in position and position[c] > k), key=position.get):
            other = reduced[position[j]]
            a, b = other[j], row.get(j, 0)
            if not b:
                continue
            g = gcd(a, b)
            fa, fb = a // g, b // g
            new = {col: v * fa for col, v in row.items()}
            for col, v in other.items():
                x = new.get(col, 0) - fb * v
                if x:
                    new[col] = x
                else:
                    new.pop(col, None)
            row = _primitive(new)
        reduced[k] = row
    pivot_set = set(pivots)
    by_free: dict[int, dict[int, Fraction]] = {f: {f: Fraction(1)} for f in range(n) if f not in pivot_set}
    for piv, row in zip(pivots, reduced):
        a = row[piv]
        for col, v in row.items():
            if col != piv:
                by_free[col][piv] = Fraction(-v, a)
    out = []
    for f in sorted(by_free):
        out.append(_primitive(_integers(by_free[f])))
    return [dict(sorted(v.items())) for v in out]


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    """The rank and the requested kernels, each vector as its nonzero entries by node ID (primitive integers); the
    certificate: the nonsingular minor's rows and columns (by node ID), and the right kernel when the answer does not
    list it."""
    ids = p._node_ids
    out: dict = {"rows": len(plan.rows), "columns": len(plan.columns), "rank": st.rank, "left_kernel": None,
                 "right_kernel": None, "certificate": None}
    if st.interruption:
        return out, []

    def vectors(kernel: list[dict[int, int]], names: list[int]) -> list[list[dict]]:
        return [[{"node": ids[names[k]], "value": v} for k, v in sorted(vec.items())] for vec in kernel]
    if plan.left:
        out["left_kernel"] = vectors(st.left, plan.rows)
    if plan.right:
        out["right_kernel"] = vectors(st.right, plan.columns)
    if plan.certificate:
        used, pivots = st.minor
        cert = {"kind": "rank", "minor": {"rows": [ids[plan.rows[i]] for i in sorted(used)],
                                          "columns": [ids[plan.columns[j]] for j in sorted(pivots)]},
                "kernel": None}
        if st.proof is not None:                         # neither kernel is listed: the smaller one bounds the rank
            cert["kernel"] = {"side": st.proof, "vectors": vectors(st.left, plan.rows) if st.proof == "left"
                              else vectors(st.right, plan.columns)}
        out["certificate"] = cert
    return out, []
