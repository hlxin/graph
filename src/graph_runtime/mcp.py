"""A dependency-free Model Context Protocol (MCP) server over stdio, after SCALE's.

``graph-runtime mcp`` exposes the :mod:`graph_runtime.tools` to any host that speaks the open Model Context
Protocol. Messages are newline-delimited JSON-RPC 2.0 on stdin and stdout; logs go to stderr.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from ._version import __version__
from .tools import TOOLS, GraphToolkit, is_error

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "GRAPH (Graph Runtime for Analysis, Provenance, Hypotheses) runs exact graph operations on immutable projections "
    "of a consumer's records, with witnesses, bounded work and reproducible result_ids. Call graph_status first. Build "
    "a projection from nodes and edges with graph_projection; run requests on it with graph_execute (reachability, "
    "typed_path, shortest_path, bottleneck_path, regular_path, derivation, personalized_pagerank, "
    "dependency_impact, pattern, support, components, biconnectivity, max_flow, matching; on "
    "a periodic cell, whose edges carry image shifts, periodicity); "
    "check a result against the records with graph_verify; after the records change, ask graph_revalidate which "
    "cached results still hold. An interrupted answer never certifies a negative, and a complete negative is scoped "
    "to the projection, not to the literature or the world. Scientific meaning stays with the application that owns "
    "the records."
)


class MCPServer:
    def __init__(self, toolkit: GraphToolkit | None = None):
        self.toolkit = toolkit or GraphToolkit()

    def handle(self, msg: Any) -> dict | None:
        """The response to one JSON-RPC message, or None for a notification."""
        if not isinstance(msg, dict):       # JSON that is not a request object: answered, never fatal
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request: not a JSON "
                                                                                      "object"}}
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:                     # a notification (notifications/initialized): no response
            return None
        params = msg.get("params")
        if params is not None and not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "invalid params: an object is "
                                                                                     "expected"}}
        try:
            return {"jsonrpc": "2.0", "id": mid, "result": self._dispatch(method, params or {})}
        except _RPCError as exc:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:  # noqa: BLE001 - never crash the host connection
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"internal error: {exc}"}}

    def _dispatch(self, method: str, params: dict) -> Any:
        if method == "initialize":
            requested = params.get("protocolVersion")
            return {"protocolVersion": requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "graph-runtime", "title": "GRAPH", "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
                              for t in TOOLS]}
        if method == "tools/call":
            name = params.get("name")
            if name not in {t["name"] for t in TOOLS}:
                raise _RPCError(-32602, f"unknown tool {name!r}")
            result = self.toolkit.call(name, params.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}],
                    "structuredContent": result, "isError": is_error(result)}
        raise _RPCError(-32601, f"method not found: {method}")

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        print(f"graph-runtime MCP server {__version__}", file=sys.stderr)
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (ValueError, RecursionError):       # not JSON, an integer too long to read, nesting too deep
                self._send(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            batch = msg if isinstance(msg, list) else [msg]
            if not batch:
                self._send(stdout, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32600, "message": "invalid request: empty batch"}})
            for m in batch:
                resp = self.handle(m)
                if resp is not None:
                    self._send(stdout, resp)

    @staticmethod
    def _send(stdout: TextIO, obj: dict) -> None:
        stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        stdout.flush()


class _RPCError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def main() -> None:
    MCPServer().serve()
