"""Certified personalized PageRank: a relevance ranking around chosen nodes whose every number is proven.

A random walk starts at a source (chosen uniformly), and at each step stops with probability alpha or else follows a
hop in scope chosen uniformly (parallel edges each count); a walk that must move from a node with no hop in scope is
lost. The personalized PageRank of a node, pi(v), is the probability that the walk stops at v. It ranks nodes by how
close and how well connected they are to the sources: the retrieval step of graph-based retrieval (HippoRAG, for one)
and a classic of link analysis (Page and Brin; Jeh and Widom; Andersen, Chung and Lang's local push, 2006).

PageRank is not computed exactly: the push algorithm stops with some probability mass not yet placed. What GRAPH
returns is exact anyway, in a different form: for each node an interval [lower, upper] that contains pi(v), proven.
The push keeps estimates p and residuals r with the invariant

    p (I - (1 - alpha) P) / alpha + r = s

(P the walk's transition matrix, s the source distribution), so pi = p + sum_u r(u) pi_u, where pi_u is the PageRank
from u alone. Every pi_u is non-negative, sums to at most 1, gives u at most 1 and any other node at most 1 - alpha;
hence ``p(v) <= pi(v) <= p(v) + r(v) + (1 - alpha) (R - r(v))``, R the total residual, and a node the push never
reached holds at most ``(1 - alpha) R``. The push runs in exact integer units, 2**-64 or finer so that the
sources share the mass equally, moving only amounts that divide evenly, so the invariant holds exactly. The
certificate is the estimates alone: a checker recomputes r from p by the invariant, exactly, and the bounds follow
when every r(v) >= 0.
"""

from __future__ import annotations

import math
from fractions import Fraction
from types import SimpleNamespace
from typing import Any

UNITS = 1 << 64                         # the push's unit of probability: 1 / 2**64, or finer for the sources


def units(sources: int) -> int:
    """The units of probability the push counts in: the least multiple of 2**64 that the sources divide, so each
    starts with exactly its share."""
    return UNITS * (sources // math.gcd(UNITS, sources))


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """Forward push in exact integer units, round by round: each round pushes, in ascending node order, every node
    whose residual moves a whole number of units (the largest multiple of ``b * hops`` it holds, for alpha = a/b),
    until the total residual is at most the tolerance, or nothing can move. A push of m units from u adds ``a * m / b``
    to p(u) and ``(b - a) * m / (b * hops)`` to the residual of the node at the end of each hop; at a node with no hop
    the walk ends and the rest of m leaves. (Pushing the largest residual first needs fewer pushes, but its priority
    queue costs more than it saves.)"""
    from .engine import _sides
    a, b = plan.alpha.numerator, plan.alpha.denominator
    rel = p._edge_rel
    sides = _sides(p, plan.fwd, plan.rev)
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, estimate={}, residual={}, pushes=0)
    est, res = st.estimate, st.residual
    st.units = total = units(len(plan.sources))
    for s in plan.sources:                                # the sources share the mass equally
        res[s] = total // len(plan.sources)
    limit = plan.tolerance * total                        # a Fraction of units
    hops_of: dict[int, list] = {}
    examined = 0
    while total > limit:
        moved = False
        for u in sorted(res):
            m = res[u]
            hops = hops_of.get(u)
            if hops is None:
                hops = []
                for lists, base, marks, ends, flag in sides:
                    hs = lists[u]
                    if hs is None:
                        hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
                    hops.extend(ends[ei] for ei in hs)
                hops_of[u] = hops
            step = b * max(len(hops), 1)
            m -= m % step
            if not m:
                continue
            why = ctl.stop()
            if why:
                st.interruption = why
                break
            moved = True
            st.pushes += 1
            examined += len(hops)
            res[u] -= m
            est[u] = est.get(u, 0) + a * m // b
            total -= m
            if hops:
                each = (b - a) * m // step
                for v in hops:
                    res[v] = res.get(v, 0) + each
                total += each * len(hops)
            if total <= limit:
                break
        if st.interruption or not moved:
            break
    st.residual = {u: x for u, x in res.items() if x}
    st.total, st.examined, st.discovered = total, examined, len(set(est) | set(st.residual))
    return st


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    """The ``top`` nodes by estimate with their intervals; bounds are compared in integer units scaled by b (alpha =
    a/b): ``b * upper = b * p + a * r + (b - a) * R``, so only the listed ones become fractions."""
    from .engine import _number
    alpha, ids = plan.alpha, p._node_ids
    a, b = alpha.numerator, alpha.denominator
    total, est, res, unit = st.total, st.estimate, st.residual, st.units

    def scaled_upper(u: int) -> int:
        return b * est.get(u, 0) + a * res.get(u, 0) + (b - a) * total

    def number(field: str, x: Fraction) -> dict:
        near, exact = _number(x, True)
        return {field: near, f"{field}_exact": exact}

    touched = sorted(set(est) | set(res), key=lambda u: (-est.get(u, 0), u))   # best estimate first, ties by ID
    top = touched[:plan.top]
    uppers = [scaled_upper(u) for u in top]
    outside = max([scaled_upper(u) for u in touched[plan.top:]] + [(b - a) * total])
    certain, floor = 0, None                              # the leading entries whose set is proven the top ones
    tail = [outside]
    for i in range(len(top) - 1, -1, -1):                 # the best upper bound after each prefix
        tail.append(max(tail[-1], uppers[i]))
    tail.reverse()
    for i, u in enumerate(top):
        floor = est.get(u, 0) if floor is None else min(floor, est.get(u, 0))
        if b * floor > tail[i + 1]:
            certain = i + 1
    ranked = [{"node": ids[u], **number("lower", Fraction(est.get(u, 0), unit)),
               **number("upper", Fraction(hi, b * unit))} for u, hi in zip(top, uppers)]
    out = {"ranked": ranked, "count": len(ranked), "certified_top": certain,
           **number("residual", Fraction(total, unit))}
    cert = None
    if plan.certificate:
        cert = {"kind": "estimates", "values": [
            {"node": ids[u], **number("value", Fraction(x, unit))} for u, x in sorted(est.items()) if x]}
    return {**out, "certificate": cert}, []
