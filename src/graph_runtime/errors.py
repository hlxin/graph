"""Errors. A request problem is reported in a result (status ``failed`` or ``unsupported``), never raised by
:func:`graph_runtime.execute`; these exceptions are raised only where there is no request to answer: building a
projection, normalizing a request on its own, or validating a hypothesis candidate."""

from __future__ import annotations


class GraphRuntimeError(Exception):
    """Base class of every GRAPH error. ``code`` is a stable, machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ProjectionError(GraphRuntimeError):
    """The records cannot form a projection (a duplicate ID, an edge whose end is not a node, a candidate record)."""


class RequestError(GraphRuntimeError):
    """A request is malformed (``status`` ``failed``) or asks for what the contract does not support (``status``
    ``unsupported``); ``detail`` holds what the message names, as data."""

    def __init__(self, status: str, code: str, message: str, detail: dict | None = None):
        super().__init__(code, message)
        self.status = status
        self.detail = detail or {}


class RetrievalError(GraphRuntimeError):
    """Text retrieval cannot proceed (:mod:`graph_runtime.retrieval`): numpy is missing, the items or a query are not
    valid, or an embedder or saved index does not match the index it is used with."""


class CandidateError(GraphRuntimeError):
    """A hypothesis candidate record is not valid; ``problems`` lists every problem found, as
    ``{"path", "code", "message"}``."""

    def __init__(self, problems: list[dict]):
        first = problems[0] if problems else {"code": "invalid_candidate", "message": "invalid candidate"}
        more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
        super().__init__(first["code"], f"{first.get('path') or '<record>'}: {first['message']}{more}")
        self.problems = problems
