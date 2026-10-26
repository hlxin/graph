"""Parity with STATES's own context extraction, on its committed example records and on structures built in memory
with STATES's importer.

For every record, root set and shell count, STATES's ``extract_context`` and the adapter's GRAPH reachability must
give the same atom images at the same shells, the same complete relations among them and the same boundary ports.
A ``max_atoms`` that the complete context exceeds makes STATES raise and GRAPH report ``interrupted``; one that fits
gives both the full context. Every GRAPH witness is checked against the lifted records. Nothing in STATES is written.
"""

from __future__ import annotations

from collections import Counter

import pytest

import states_graph_adapter as adapter
from graph_runtime import COMPLETED, INTERRUPTED, execute, verify_result

SHELLS = (0, 1, 2, 3)


def states_view(ctx: dict) -> tuple[dict, set, set]:
    """STATES's context as comparable sets: atom image -> shell, relations, ports (by source edge and atom images)."""
    handle = {n["id"]: (n["source_atom"], tuple(n["image"])) for n in ctx["nodes"]}
    nodes = {(n["source_atom"], tuple(n["image"])): n["shell"] for n in ctx["nodes"]}
    edges = {(e["source_edge"], tuple(sorted((handle[e["i"]], handle[e["j"]])))) for e in ctx["edges"]}
    ports = {(p["source_edge"], handle[p["inside"]], (p["outside"]["source_atom"], tuple(p["outside"]["image"])))
             for p in ctx["boundary_ports"]}
    return nodes, edges, ports


def graph_view(ctx: dict) -> tuple[dict, set, set]:
    nodes = {(n["atom"], tuple(n["image"])): n["shell"] for n in ctx["nodes"]}
    edges = {(e["source_edge"], tuple(sorted((a, tuple(img)) for a, img in e["ends"]))) for e in ctx["edges"]}
    ports = {(p["source_edge"], (p["inside"][0], tuple(p["inside"][1])), (p["outside"][0], tuple(p["outside"][1])))
             for p in ctx["ports"]}
    return nodes, edges, ports


def check_witnesses(state: dict, ctx: dict) -> int:
    """Every via of the GRAPH context is a STATES neighbor edge followed between the two atom images it joins."""
    checked = 0
    for n in ctx["nodes"]:
        via = n["via"]
        if via is None:
            assert n["shell"] == 0
            continue
        e = state["edges"][via["source_edge"]]
        assert e["kind"] == adapter.NEIGHBOR
        base = tuple(via["edge_image"])
        far = tuple(b + s for b, s in zip(base, e["shift"]))
        ends = ((e["i"], base), (e["j"], far))
        here, there = (ends if via["direction"] == "forward" else ends[::-1])
        assert (via["from"][0], tuple(via["from"][1])) == here and (n["atom"], tuple(n["image"])) == there
        checked += 1
    return checked


def compare(state: dict, roots, tally: Counter) -> None:
    from states.context import extract_context
    from states.model import StateError
    for shells in SHELLS:
        want = extract_context(state, roots=roots, shells=shells, max_atoms=10**6)
        got = adapter.context(state, roots=roots, shells=shells, max_atoms=10**6)
        assert got["status"] == COMPLETED
        assert graph_view(got) == states_view(want), (shells, roots)
        assert got["root_policy"] == want["root_policy"]
        tally["witnesses"] += check_witnesses(state, got)
        n = len(want["nodes"])
        assert adapter.context(state, roots=roots, shells=shells, max_atoms=n)["status"] == COMPLETED
        extract_context(state, roots=roots, shells=shells, max_atoms=n)             # fits: STATES answers too
        if n > 1:
            cut = adapter.context(state, roots=roots, shells=shells, max_atoms=n - 1)
            assert cut["status"] == INTERRUPTED and cut["interruption"]["reason"] == "output_limit"
            assert "edges" not in cut and len(cut["nodes"]) == n - 1
            assert all(want_shell == states_view(want)[0][(x["atom"], tuple(x["image"]))]
                       for x in cut["nodes"] for want_shell in [x["shell"]])
            with pytest.raises(StateError):
                extract_context(state, roots=roots, shells=shells, max_atoms=n - 1)
            tally["limits"] += 1
        tally["contexts"] += 1
        tally["atom_images"] += n
        tally["relations"] += len(want["edges"]) + len(want["boundary_ports"])


def root_sets(state: dict) -> list:
    """The deposited contacts' roots when there are any; explicit roots otherwise, and also an explicit root moved to
    another image along every periodic direction."""
    substrate = [i for i, a in enumerate(state["atoms"]) if a["role"] == "substrate"]
    if not substrate:
        return []
    out = [None] if any(state["atoms"][s["atom"]]["role"] == "substrate"
                        for c in state["contacts"] for s in c["sites"]) else []
    out.append([substrate[0]])
    moved = [1 if periodic else 0 for periodic in state["pbc"]]
    if any(moved):
        out.append([{"atom": substrate[-1], "image": moved}, substrate[len(substrate) // 2]])
    return out


def test_committed_example_records_match_states(states_repo):
    from states.io import load_state
    tally: Counter = Counter()
    paths = sorted((states_repo / "examples" / "data").glob("*.states")) + \
        sorted((states_repo / "examples" / "demo_output").glob("*.states"))
    for path in paths:
        state = load_state(path)
        for roots in root_sets(state):
            compare(state, roots, tally)
        tally["records"] += 1
    print(dict(tally))
    assert tally["records"] >= 15 and tally["contexts"] >= 150 and tally["limits"] >= 100


def built_structures():
    """Slabs of five facets with adsorbates at several sites, and small bulk cells whose atoms neighbor their own
    images, built in memory with ASE and imported with STATES's own importer."""
    from ase import Atoms
    from ase.build import add_adsorbate, bcc110, bulk, fcc100, fcc110, fcc111, hcp0001
    from states.atomio import from_ase
    cases = []
    for build, metal, site in ((fcc111, "Pt", "fcc"), (fcc111, "Pt", "hcp"), (fcc111, "Cu", "ontop"),
                               (fcc100, "Cu", "hollow"), (fcc100, "Ni", "bridge"), (fcc110, "Ni", "longbridge"),
                               (hcp0001, "Ru", "fcc"), (bcc110, "Fe", "hollow")):
        for size in ((2, 2, 3), (3, 3, 4)):
            for adsorbate in ("O", "H", "CO"):
                slab = build(metal, size=size, vacuum=7.0)
                n = len(slab)
                molecule = Atoms("CO", positions=[(0, 0, 0), (0, 0, 1.15)]) if adsorbate == "CO" else adsorbate
                add_adsorbate(slab, molecule, 1.6 if adsorbate != "H" else 1.0, site)
                cases.append((f"{metal}-{build.__name__}-{site}-{size}-{adsorbate}",
                              from_ase(slab, adsorbate_indices=list(range(n, len(slab))))))
    for name, crystal in (("Cu-fcc-primitive", bulk("Cu")), ("Fe-bcc-primitive", bulk("Fe")),
                          ("Cu-fcc-cubic", bulk("Cu", cubic=True)), ("Ru-hcp", bulk("Ru"))):
        cases.append((name, from_ase(crystal, roles=["substrate"] * len(crystal))))
    return cases


def test_structures_built_with_the_states_importer_match_states(states_repo):
    tally: Counter = Counter()
    for name, state in built_structures():
        for roots in root_sets(state):
            compare(state, roots, tally)
        tally["records"] += 1
        tally["self_image_edges"] += sum(1 for e in state["edges"] if e["i"] == e["j"] and any(e["shift"]))
    print(dict(tally))
    assert tally["records"] >= 50 and tally["contexts"] >= 400 and tally["self_image_edges"] > 0


def test_witnesses_check_against_the_lifted_records(states_repo):
    from states.io import load_state
    state = load_state(states_repo / "examples" / "data" / "c7h8_on_pt_alloy.states")
    refs, _ = adapter.roots_of(state)
    p, lift = adapter.lifted_projection(state, refs, 3)
    res = execute(p, p.request("reachability", sources=[adapter.image_id(a, img) for a, img in refs],
                               scope=adapter.SCOPE, max_depth=3))
    assert res.complete and res.answer["count"] > 50
    assert verify_result(res, nodes=lift["nodes"], edges=lift["edges"]) == []
    deepest = max(res.answer["reached"], key=lambda e: e["depth"])
    assert len(res.path_to(deepest["node"])) == 3


def test_a_changed_record_makes_its_lift_stale(states_repo):
    import copy
    from states.io import load_state
    state = load_state(states_repo / "examples" / "data" / "o_on_oxide.states")
    refs, _ = adapter.roots_of(state)
    p, lift = adapter.lifted_projection(state, refs, 2)
    edited = copy.deepcopy(state)
    edited["atoms"][0]["attrs"] = {**edited["atoms"][0]["attrs"], "note": "edited"}
    req = p.request("reachability", sources=[adapter.image_id(a, img) for a, img in refs], scope=adapter.SCOPE)
    assert execute(p, req, current_bindings=adapter.bindings_of(state, lift["box"])).status == COMPLETED
    res = execute(p, req, current_bindings=adapter.bindings_of(edited, lift["box"]))
    assert (res.status, res.error["code"]) == ("stale_snapshot", "bindings_changed")


def test_the_periodic_cell_answers_every_context_as_the_lift_does(states_repo):
    """The same contexts on the record's cell, never lifted (a periodic projection): the same atom images at the same
    shells, the same witnesses, relations and ports, and the same interruption, for every root set and shell count of
    every committed record and built structure."""
    from states.io import load_state
    paths = sorted((states_repo / "examples" / "data").glob("*.states")) + \
        sorted((states_repo / "examples" / "demo_output").glob("*.states"))
    states = [load_state(path) for path in paths] + [state for _, state in built_structures()]
    tally: Counter = Counter()
    for state in states:
        for roots in root_sets(state):
            for shells in SHELLS:
                lifted = adapter.context(state, roots=roots, shells=shells, max_atoms=10**6)
                cell = adapter.context(state, roots=roots, shells=shells, max_atoms=10**6, periodic=True)
                assert cell["status"] == COMPLETED and cell["box"] is None
                assert graph_view(cell) == graph_view(lifted)
                assert cell["nodes"] == lifted["nodes"]                      # shells and witnesses, one for one
                n = len(cell["nodes"])
                if n > 1:
                    cut = adapter.context(state, roots=roots, shells=shells, max_atoms=n - 1, periodic=True)
                    assert cut["status"] == INTERRUPTED and cut["nodes"] == adapter.context(
                        state, roots=roots, shells=shells, max_atoms=n - 1)["nodes"]
                tally["contexts"] += 1
                tally["atom_images"] += n
    print(dict(tally))
    assert tally["contexts"] >= 700


def test_periodic_witnesses_check_against_the_cell(states_repo):
    from states.io import load_state
    state = load_state(states_repo / "examples" / "data" / "c7h8_on_pt_alloy.states")
    refs, _ = adapter.roots_of(state)
    pp = adapter.periodic_projection(state)
    res = execute(pp, pp.request("reachability", sources=[adapter.image_id(a, img) for a, img in refs],
                                 scope=adapter.SCOPE, max_depth=3))
    assert res.complete and res.answer["count"] > 50
    cell = pp.to_dict()
    assert verify_result(res, nodes=cell["nodes"], edges=cell["edges"]) == []
