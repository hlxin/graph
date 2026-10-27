"""The tool surface: the toolkit's tools (results are data, nothing is written, a session keeps what it built), the
MCP server's JSON-RPC handling over stdio, and the command line."""

import io
import json

import pytest

from graph_runtime import CONTRACT, OPERATIONS
from graph_runtime.cli import main
from graph_runtime.mcp import MCPServer
from graph_runtime.tools import TOOL_NAMES, TOOLS, GraphToolkit

RECORDS = {"nodes": ["a", "b", "c", "x"],
           "edges": [{"id": "e1", "source": "a", "target": "b", "relation": "R"},
                     {"id": "e2", "source": "b", "target": "c", "relation": "R"}],
           "bindings": {"source": "tools-test@1"}}
REACH = {"operation": "reachability", "sources": ["a"], "scope": [["R", "forward"]]}


@pytest.fixture
def kit():
    return GraphToolkit()


def test_every_tool_is_described_by_json_schema():
    import jsonschema
    assert len(TOOL_NAMES) == len(set(TOOL_NAMES)) == 10
    for t in TOOLS:
        jsonschema.Draft202012Validator.check_schema(t["input_schema"])
        assert t["name"].startswith("graph_") and len(t["description"]) > 40


def test_status_and_describe(kit):
    st = kit.call("graph_status")
    assert st["contract"] == CONTRACT and st["operations"] == list(OPERATIONS) and st["projections"] == []
    assert set(kit.call("graph_describe")["kinds"]) == {"request", "result", "projection", "periodic_projection",
                                                        "hypothesis_candidate"}
    assert kit.call("graph_describe", {"name": "result"})["schema"]["$ref"] == "#/$defs/result"
    assert kit.call("graph_describe", {"name": "nothing"})["error"]["code"] == "unknown_kind"


def test_a_session_keeps_the_projection_it_built(kit):
    built = kit.call("graph_projection", RECORDS)
    assert built["nodes"] == 4 and built["edges"] == 2 and "snapshot" not in built
    res = kit.call("graph_execute", {"request": {**REACH, "projection": built["identity"]}})
    assert res["status"] == "completed" and [e["node"] for e in res["answer"]["reached"]] == ["a", "b", "c"]
    assert kit.call("graph_status")["projections"] == [{"identity": built["identity"], "nodes": 4, "edges": 2}]
    inline = kit.call("graph_execute", {"request": REACH, "projection": RECORDS})
    assert inline["result_id"] == res["result_id"]                 # the same projection and request
    other = GraphToolkit().call("graph_execute", {"request": {**REACH, "projection": built["identity"]}})
    assert other["error"]["code"] == "unknown_projection"


def test_projections_come_from_files_of_records_or_snapshots(kit, tmp_path):
    (tmp_path / "records.json").write_text(json.dumps(RECORDS))
    a = kit.call("graph_projection", {"path": str(tmp_path / "records.json"), "snapshot": True})
    (tmp_path / "snapshot.json").write_text(json.dumps(a["snapshot"]))
    b = kit.call("graph_projection", {"path": str(tmp_path / "snapshot.json")})
    assert a["identity"] == b["identity"]
    assert kit.call("graph_projection", {"path": str(tmp_path / "missing.json")})["error"]["code"] == "invalid_input"
    assert kit.call("graph_projection", {})["error"]["code"] == "invalid_arguments"
    bad = kit.call("graph_projection", {"nodes": ["a"], "edges": [{"id": "e", "source": "a", "target": "z",
                                                                    "relation": "R"}]})
    assert bad["error"]["code"] == "unknown_endpoint" or "error" in bad


def test_problems_come_back_as_data(kit):
    assert kit.call("graph_nothing")["error"]["code"] == "unknown_tool"
    assert kit.call("graph_execute", {"bogus": 1})["error"]["code"] == "invalid_arguments"
    assert kit.call("graph_execute", ["not", "an", "object"])["error"]["code"] == "invalid_arguments"
    failed = kit.call("graph_execute", {"request": {"operation": "reachability", "sources": ["zz"],
                                                    "scope": [["R", "forward"]]}, "projection": RECORDS})
    assert failed["status"] == "failed" and failed["error"]["code"] == "unknown_node"    # a result, as execute says


def test_verify_and_revalidate(kit):
    built = kit.call("graph_projection", RECORDS)
    res = kit.call("graph_execute", {"request": {**REACH, "projection": built["identity"]}})
    assert kit.call("graph_verify", {"result": res}) == {"problems": [], "checked_against": built["identity"]}
    more = {**RECORDS, "edges": RECORDS["edges"] + [{"id": "e3", "source": "c", "target": "x", "relation": "R"}]}
    edited = kit.call("graph_projection", more)
    out = kit.call("graph_revalidate", {"results": [res], "projection": {"identity": edited["identity"]},
                                        "old": {"identity": built["identity"]}})
    (one,) = out["revalidations"]
    assert one["verdict"] == "fails" and one["reasons"] == [{"code": "reaches_unlisted", "edge": "e3", "from": "c",
                                                             "to": "x"}]
    assert out["diff"]["added_edges"] == ["e3"]
    stale = kit.call("graph_verify", {"result": res, "projection": {"identity": edited["identity"]}})
    assert stale["problems"] == ["incomplete: 'x' is one hop (e3) from 'c' at depth 2, but is not listed"]
    forged = {**res, "answer": {**res["answer"], "count": 7}}
    assert kit.call("graph_revalidate", {"results": [forged], "projection": more})["error"]["code"] == "invalid_input"


def test_search_names_the_nodes_a_traversal_starts_from(kit, tmp_path):
    pytest.importorskip("numpy")
    items = [{"id": "q1", "record": "a", "text": "sulphur poisons the copper catalyst"},
             {"id": "q2", "record": "c", "text": "zinc oxide"}, {"id": "q3", "record": "x", "text": "iron"}]
    out = kit.call("graph_search", {"query": "sulfur poisoning", "items": items, "k": 2,
                                    "variants": {"sulphur": "sulfur"}, "projection": RECORDS})
    assert out["hits"][0]["id"] == "q1" and out["seeds"][0] == "a"
    again = kit.call("graph_search", {"query": "zinc", "index": out["index_id"], "mode": "keyword"})
    assert [h["id"] for h in again["hits"]] == ["q2"]
    assert kit.call("graph_search", {"query": "x"})["error"]["code"] == "invalid_arguments"


def test_the_conformance_suite_runs_as_a_tool(kit):
    report = kit.call("graph_conformance")
    assert report["failures"] == [] and report["passed"] == report["cases"] >= 74


# ------------------------------------------------------------------------------------------ MCP
def rpc(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params else {})})


def test_mcp_initialize_list_and_call():
    s = MCPServer()
    init = rpc(s, "initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert init["protocolVersion"] == "2025-03-26" and init["serverInfo"]["name"] == "graph-runtime"
    assert rpc(s, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-06-18"
    assert rpc(s, "ping")["result"] == {}
    tools = rpc(s, "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == list(TOOL_NAMES) and all("inputSchema" in t for t in tools)
    built = rpc(s, "tools/call", {"name": "graph_projection", "arguments": RECORDS})["result"]
    assert built["isError"] is False and json.loads(built["content"][0]["text"]) == built["structuredContent"]
    ident = built["structuredContent"]["identity"]
    res = rpc(s, "tools/call", {"name": "graph_execute", "arguments": {"request": {**REACH, "projection": ident}}})
    assert res["result"]["structuredContent"]["status"] == "completed"          # the session kept the projection
    err = rpc(s, "tools/call", {"name": "graph_execute", "arguments": {"request": REACH}})["result"]
    assert err["isError"] is True
    failed = rpc(s, "tools/call", {"name": "graph_execute", "arguments": {
        "request": {**REACH, "sources": ["zz"], "projection": ident}}})["result"]
    assert failed["isError"] is False and failed["structuredContent"]["status"] == "failed"   # a result, not an error
    assert rpc(s, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert rpc(s, "resources/list")["error"]["code"] == -32601
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert s.handle([1])["error"]["code"] == -32600
    assert rpc(s, "tools/call", ["not", "an", "object"])["error"]["code"] == -32602


def test_mcp_over_stdio():
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), "not json", "[]",
             json.dumps([{"jsonrpc": "2.0", "id": 2, "method": "ping"}, {"jsonrpc": "2.0", "method": "note"}]), ""]
    out = io.StringIO()
    MCPServer().serve(io.StringIO("\n".join(lines) + "\n"), out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert replies[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert replies[1]["error"]["code"] == -32700 and replies[2]["error"]["code"] == -32600
    assert replies[3] == {"jsonrpc": "2.0", "id": 2, "result": {}} and len(replies) == 4


# ------------------------------------------------------------------------------------------ command line
def test_the_command_line(capsys, tmp_path):
    assert main(["call", "graph_status"]) == 0
    assert json.loads(capsys.readouterr().out)["contract"] == CONTRACT
    args = tmp_path / "args.json"
    args.write_text(json.dumps({"request": REACH, "projection": RECORDS}))
    assert main(["call", "graph_execute", f"@{args}"]) == 0
    assert json.loads(capsys.readouterr().out)["answer"]["count"] == 3
    assert main(["call", "graph_execute", "{}"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)
    assert main(["call", "graph_execute", "{not json"]) == 2
    assert "cannot read the arguments" in capsys.readouterr().err
    assert main(["tools"]) == 0 and len(json.loads(capsys.readouterr().out)) == 10
    assert main([]) == 2 and main(["what"]) == 2


def test_a_periodic_cell_is_a_projection_too(kit):
    cell = {"nodes": [{"id": "A", "types": ["Pt"]}],
            "edges": [{"id": "x", "source": "A", "target": "A", "relation": "neighbor", "shift": [1, 0]},
                      {"id": "y", "source": "A", "target": "A", "relation": "neighbor", "shift": [0, 1]}]}
    built = kit.call("graph_projection", cell)
    assert built["dimensions"] == 2 and built["periodic"] == [True, True] and built["nodes"] == 1
    res = kit.call("graph_execute", {"request": {"operation": "reachability", "sources": ["A@0,0"], "max_depth": 2,
                                                 "scope": [["neighbor", "forward"], ["neighbor", "reverse"]]},
                                     "projection": {"identity": built["identity"]}})
    assert res["status"] == "completed" and res["answer"]["count"] == 13
    assert kit.call("graph_verify", {"result": res})["problems"] == []


CHAIN = {"nodes": [f"n{i:02d}" for i in range(40)],
         "edges": [{"id": f"e{i:02d}", "source": f"n{i:02d}", "target": f"n{i + 1:02d}", "relation": "R"}
                   for i in range(39)]}


def test_a_summary_is_small_and_the_session_keeps_the_whole(kit):
    p = kit.call("graph_projection", CHAIN)["identity"]
    req = {"operation": "reachability", "sources": ["n05"], "scope": [["R", "forward"], ["R", "reverse"]]}
    small = kit.call("graph_execute", {"request": req, "projection": {"identity": p}, "view": "summary", "limit": 3})
    assert small["summary"]["count"] == 40 and small["cached"] is False
    assert small["summary"]["items"] == [{"node": "n05", "depth": 0}, {"node": "n04", "depth": 1},
                                         {"node": "n06", "depth": 1}] and small["summary"]["more"]
    assert small["summary"]["by_depth"] == [[0, 1], [1, 2], [2, 2]]
    assert len(json.dumps(small)) < len(json.dumps(kit.call("graph_result", {"result_id": small["result_id"]}))) / 5
    again = kit.call("graph_execute", {"request": req, "projection": {"identity": p}, "view": "summary"})
    assert again["cached"] is True and again["result_id"] == small["result_id"]
    whole = kit.call("graph_result", {"result_id": small["result_id"]})
    assert whole["answer"]["count"] == 40 and whole["result_id"] == small["result_id"]
    page = kit.call("graph_result", {"result_id": small["result_id"], "field": "reached", "offset": 38, "limit": 5})
    assert page["total"] == 40 and [e["node"] for e in page["items"]] == ["n38", "n39"]
    path = kit.call("graph_result", {"result_id": small["result_id"], "path_to": "n08"})
    assert [h["edge"] for h in path["hops"]] == ["e05", "e06", "e07"]
    assert kit.call("graph_result", {"result_id": "sha256:" + "0" * 64})["error"]["code"] == "unknown_result"
    assert kit.call("graph_result", {"result_id": small["result_id"], "field": "nope"})["error"]["code"] \
        == "invalid_arguments"
    assert kit.call("graph_result", {"result_id": small["result_id"], "path_to": "zz"})["error"]["code"] \
        == "unknown_node"


def test_a_batch_answers_each_request_in_order(kit):
    p = {"identity": kit.call("graph_projection", CHAIN)["identity"]}
    out = kit.call("graph_batch", {"projection": p, "requests": [
        {"operation": "regular_path", "sources": ["n00"], "path": "R{2}/R*", "targets": ["n10"]},
        {"operation": "shortest_path", "sources": ["n00"], "targets": ["n04"], "scope": [["R", "forward"]],
         "costs": {"by_relation": {"R": 0.5}}},
        {"operation": "derivation", "targets": ["n00", "n39"], "scope": [["R", "forward"]]},
        {"operation": "pagerank"},
        "not a request"]})
    assert out["count"] == 5
    walk, cost, derivation, unsupported, bad = out["results"]
    assert walk["summary"]["found"] is True and walk["summary"]["length"] == 10
    assert cost["summary"]["cost_exact"] == "2" and cost["summary"]["length"] == 4
    assert derivation["summary"]["derived"] == [{"node": "n00", "value": 39}, {"node": "n39", "value": 0}]
    assert unsupported["status"] == "unsupported"
    assert bad["error"]["code"] == "invalid_arguments"
    full = kit.call("graph_batch", {"projection": p, "view": "full", "requests": [
        {"operation": "reachability", "sources": ["n00"], "scope": [["R", "forward"]], "max_depth": 1}]})
    assert full["results"][0]["answer"]["count"] == 2


def test_summaries_carry_a_negative_cycle_and_a_matching_cost():
    """A deep Codex review's cases: an unbounded shortest path summarized as found with nothing else, and a
    weighted matching's cost left out."""
    from graph_runtime import Projection, execute
    from graph_runtime.tools import summarize
    p = Projection.build(nodes=["s"], edges=[{"id": "e", "source": "s", "target": "s", "relation": "R"}])
    res = execute(p, p.request("shortest_path", sources=["s"], targets=["s"], scope=[("R", "forward")],
                               costs={"by_relation": {"R": -1}}))
    assert res.answer["negative_cycle"] is not None
    assert summarize(res)["summary"]["negative_cycle"] == {"cost": -1, "edges": ["e"], "more": False}
    p = Projection.build(nodes=["l", "r"], edges=[{"id": "e", "source": "l", "target": "r", "relation": "R"}])
    res = execute(p, p.request("matching", left={"nodes": ["l"]}, scope=[("R", "forward")],
                               costs={"by_edge": {"e": -0.5}}))
    summary = summarize(res)["summary"]
    assert summary["cost"] == -0.5 and summary["cost_exact"] == "-1/2"
