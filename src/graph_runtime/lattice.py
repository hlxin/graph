"""Integer lattices, exactly: the Hermite normal form of a set of integer vectors (a canonical basis of the lattice
they generate), membership, and the coefficients that write a lattice vector in a basis. Plain Python integers, no
floating point: what periodicity analysis needs to name a translation lattice and to prove what it contains.

A lattice here is the set of integer combinations of some vectors of Z^d. Its Hermite normal form (row style) is the
unique basis whose rows are in echelon form, each pivot positive and every entry above a pivot reduced to the range
[0, pivot). Two sets of vectors generate the same lattice exactly when their Hermite normal forms are equal.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from itertools import combinations
from math import gcd
from typing import Any


def hermite(vectors: Iterable[Sequence[int]], dimensions: int) -> list[tuple[int, ...]]:
    """The Hermite normal form of the lattice the vectors generate: its rows, top to bottom (no zero rows)."""
    return hermite_combinations(list(vectors), dimensions)[0]


def hermite_combinations(vectors: Sequence[Sequence[int]], dimensions: int, stop: Callable[[], Any] | None = None
                         ) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]]] | None:
    """The Hermite normal form of the lattice the vectors generate, and for each of its rows the integers that write it
    as a combination of the vectors (``sum(c[j] * vectors[j]) == row``): a witness that the row is in the lattice.

    Each row carries its combination sparsely, as {vector index: integer}, so a row operation costs the dimensions
    and the vectors it involves, not the number of vectors. ``stop``, when given, is called before each row operation
    and returns why it must not happen (then the reduction returns None) or None."""
    n = len(vectors)
    for v in vectors:
        if len(v) != dimensions:
            raise ValueError(f"a vector of {len(v)} components in {dimensions} dimensions")

    def minus(r: tuple, q: int, pivot: tuple) -> tuple:
        combo = dict(r[1])
        for j, c in pivot[1].items():
            x = combo.get(j, 0) - q * c
            if x:
                combo[j] = x
            else:
                combo.pop(j, None)
        return [a - q * b for a, b in zip(r[0], pivot[0])], combo

    rows = [(list(v), {i: 1}) for i, v in enumerate(vectors) if any(v)]
    basis: list[tuple] = []
    col = 0
    while rows and col < dimensions:
        live = [r for r in rows if r[0][col]]
        if not live:
            col += 1
            continue
        rest = [r for r in rows if not r[0][col]]
        while len(live) > 1:                              # Euclid on the column: keep one row with a nonzero entry
            live.sort(key=lambda r: abs(r[0][col]))
            pivot = live[0]
            out = [pivot]
            for r in live[1:]:
                if stop is not None and stop():
                    return None
                r = minus(r, r[0][col] // pivot[0][col], pivot)
                (out if r[0][col] else rest).append(r)
            live = out
        pivot = live[0]
        if pivot[0][col] < 0:
            pivot = ([-a for a in pivot[0]], {j: -c for j, c in pivot[1].items()})
        basis.append(pivot)
        rows = [r for r in rest if any(r[0])]
        col += 1
    for i, row in enumerate(basis):                       # reduce the entries above each pivot to [0, pivot)
        c = next(j for j, x in enumerate(row[0]) if x)
        for k in range(i):
            q = basis[k][0][c] // row[0][c]
            if q:
                if stop is not None and stop():
                    return None
                basis[k] = minus(basis[k], q, row)
    return [tuple(r[0]) for r in basis], [tuple(r[1].get(j, 0) for j in range(n)) for r in basis]


def reduced(basis: Sequence[Sequence[int]], vector: Sequence[int]) -> tuple[int, ...]:
    """The canonical representative of ``vector``'s coset modulo the lattice a Hermite-normal-form basis generates:
    the vector less the basis rows, top to bottom, that bring its entry at each pivot into [0, pivot). Two vectors
    differ by a lattice vector exactly when their representatives are equal."""
    rest = list(vector)
    for row in basis:
        c = next(j for j, x in enumerate(row) if x)
        q = rest[c] // row[c]
        if q:
            rest = [a - q * b for a, b in zip(rest, row)]
    return tuple(rest)


def coefficients(basis: Sequence[Sequence[int]], vector: Sequence[int]) -> list[int] | None:
    """Integers c with sum(c_i * basis_i) == vector, for a basis in Hermite normal form; None when the vector is not
    in the lattice."""
    rest = list(vector)
    out = []
    for row in basis:
        c = next(j for j, x in enumerate(row) if x)
        for j in range(c):                                # entries left of this pivot must already be cleared
            if rest[j]:
                return None
        if rest[c] % row[c]:
            return None
        q = rest[c] // row[c]
        out.append(q)
        rest = [a - q * b for a, b in zip(rest, row)]
    return out if not any(rest) else None


def contains(basis: Sequence[Sequence[int]], vector: Sequence[int]) -> bool:
    """Whether a vector is in the lattice a Hermite-normal-form basis generates."""
    return coefficients(basis, vector) is not None


def copies(basis: Sequence[Sequence[int]]) -> int:
    """The index of a lattice in its saturation (the integer vectors of the space it spans): how many of its
    translated copies run through the same space, the degree of interpenetration of a periodic net's component. It is
    the gcd of the basis's maximal minors (1 for the zero lattice)."""
    rows = [list(r) for r in basis]
    if not rows:
        return 1
    out = 0
    for cols in combinations(range(len(rows[0])), len(rows)):
        out = gcd(out, _determinant([[r[c] for c in cols] for r in rows]))
    return abs(out)


def _determinant(m: list[list[int]]) -> int:
    """An integer determinant, by Bareiss's fraction-free elimination."""
    m = [row[:] for row in m]
    n, sign, prev = len(m), 1, 1
    for k in range(n - 1):
        if m[k][k] == 0:
            swap = next((i for i in range(k + 1, n) if m[i][k]), None)
            if swap is None:
                return 0
            m[k], m[swap] = m[swap], m[k]
            sign = -sign
        for i in range(k + 1, n):
            for j in range(k + 1, n):
                m[i][j] = (m[i][j] * m[k][k] - m[i][k] * m[k][j]) // prev
        prev = m[k][k]
    return sign * m[n - 1][n - 1]
