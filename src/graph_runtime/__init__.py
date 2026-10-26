"""GRAPH: Graph Runtime for Analysis, Provenance, Hypotheses. Reusable graph algorithms for scientific software.

GRAPH executes graph operations for scientific applications and keeps the computational contract the same across them:
explicit inputs (an immutable :class:`Projection`, with optional type and relation hierarchies, or a
:class:`PeriodicProjection` of one cell of an infinite periodic graph, and a normalized :class:`Request`), nine exact
operations (reachability, typed path, shortest path, bottleneck path, regular path, dependency impact, tree patterns,
support under failure and lightest derivations) with structural witnesses, bounded work, reproducible results, and
witnesses that can be checked against the input records (:func:`verify_result`); certificates prove negatives and
optima, and a cached result is revalidated on an edited projection without a new search (:func:`revalidate`). It
transports hypothesis candidates (:class:`HypothesisCandidate`) without ever turning them into assertions. The
optional :mod:`graph_runtime.retrieval` (numpy) ranks text items that name a projection's records, so a search can
choose where a traversal starts. Scientific meaning stays with the applications that own it; GRAPH imports none of
their vocabularies.

>>> p = Projection.build(nodes=["a", "b"], edges=[{"id": "e1", "source": "a", "target": "b", "relation": "R"}])
>>> r = execute(p, p.request("reachability", sources=["a"], scope=[("R", "forward")]))
>>> r.status, r.reachable("b"), r.path_to("b")[0]["edge"]
('completed', True, 'e1')
"""

from ._version import __version__
from .engine import CancelToken, execute
from .errors import CandidateError, GraphRuntimeError, ProjectionError, RequestError, RetrievalError
from .hypotheses import (HypothesisCandidate, order_candidates, staleness, validate_candidate, verify_revisions,
                         witness_refs)
from .periodic import PeriodicProjection
from .projection import CONTRACT, FORWARD, REVERSE, EdgeRecord, NodeRecord, Projection
from .request import OPERATIONS, Request, normalize_request
from .result import COMPLETED, FAILED, INTERRUPTED, STALE, STATUSES, UNSUPPORTED, Result
from .revalidate import ProjectionDiff, Revalidation, revalidate, revalidate_all
from .schemas import json_schema
from .verify import verify_result

__all__ = [
    "CONTRACT", "OPERATIONS", "STATUSES", "COMPLETED", "INTERRUPTED", "UNSUPPORTED", "FAILED", "STALE", "FORWARD",
    "REVERSE", "Projection", "PeriodicProjection", "NodeRecord", "EdgeRecord", "Request", "normalize_request",
    "execute", "CancelToken",
    "Result", "verify_result", "revalidate", "revalidate_all", "Revalidation", "ProjectionDiff",
    "HypothesisCandidate", "validate_candidate", "verify_revisions", "staleness", "order_candidates", "witness_refs",
    "json_schema", "GraphRuntimeError", "ProjectionError", "RequestError", "RetrievalError", "CandidateError",
    "__version__",
]
