"""The command line: the tools as JSON in, JSON out, the MCP server, and the conformance suite.

    graph-runtime tools                         # the tools and their JSON Schemas
    graph-runtime call <tool> '<json>'          # one tool call; '-' reads the arguments from stdin
    graph-runtime call graph_execute @args.json # arguments from a file
    graph-runtime mcp                           # the MCP server over stdio
    graph-runtime conformance                   # the conformance suite against this runtime

A call prints the tool's result as JSON and exits 0, or 1 when the tool returns an error (a request that fails is a
result, whose status says so); a command line that cannot be read exits 2. One call is one session: to run several requests on one projection, pass it inline (or as a file)
to each, or use the MCP server, whose session keeps it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .tools import TOOLS, GraphToolkit, is_error


def _arguments(text: str) -> dict:
    if text == "-":
        text = sys.stdin.read()
    elif text.startswith("@"):
        text = Path(text[1:]).expanduser().read_text(encoding="utf-8")
    value = json.loads(text or "{}")
    if not isinstance(value, dict):
        raise ValueError("the arguments are a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0 if args else 2
    command = args[0]
    if command == "tools":
        print(json.dumps(TOOLS, indent=1, ensure_ascii=False))
        return 0
    if command == "mcp":
        from .mcp import main as serve
        serve()
        return 0
    if command == "conformance":
        report = GraphToolkit().call("graph_conformance", {})
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 0 if not report.get("failures") and not is_error(report) else 1
    if command == "call" and len(args) in (2, 3):
        try:
            arguments = _arguments(args[2] if len(args) == 3 else "{}")
        except (OSError, ValueError) as e:
            print(f"graph-runtime: cannot read the arguments: {e}", file=sys.stderr)
            return 2
        result = GraphToolkit().call(args[1], arguments)
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return 1 if is_error(result) else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
