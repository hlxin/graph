"""STATES's adapter to GRAPH: a record's periodic substrate graph, lifted to atom images in a finite box, as a GRAPH
projection; and context extraction (the substrate within some shells of a binding site) as bounded reachability.

This adapter belongs to STATES, not to GRAPH: it reads STATES records (atoms and their roles, edges and their kinds
and image shifts, adsorption contacts) so that the GRAPH runtime never has to. It is staged in the GRAPH repository
until STATES takes it in under its own review (docs/STATES_INTEGRATION.md). It only reads STATES records.

The workflow is STATES's ``context.extract_context``: from the substrate incidences of the adsorption contacts (or
explicit roots), every substrate atom image within ``shells`` hops of undirected ``neighbor`` edges, the complete
relations among them, and boundary ports for the relations that leave them. A periodic substrate graph is
infinite; its lift to images is finite here because a walk of ``shells`` hops moves an image by at most ``shells``
times the largest shift of a neighbor edge, so the box this adapter lifts (one hop wider, for the ports) holds every
walk the context can take. GRAPH answers the reachability (its ``max_depth`` is the shells, its ``max_results`` the
``max_atoms``) with a witness for every atom image; STATES keeps what a context means.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import product
from typing import Any

from graph_runtime import COMPLETED, Projection, Result, execute

NEIGHBOR = "neighbor"
SCOPE = [{"relation": NEIGHBOR, "direction": "forward"}, {"relation": NEIGHBOR, "direction": "reverse"}]
ZERO = (0, 0, 0)


class Unsupported(ValueError):
    """Roots or a record this adapter does not lift (STATES would refuse them too)."""


def image_id(atom: int, image: Sequence[int]) -> str:
    """The GRAPH node ID of an atom image: ``<atom>@<i>,<j>,<k>``."""
    return f"{atom}@{image[0]},{image[1]},{image[2]}"


def parse_image_id(node: str) -> tuple[int, tuple[int, int, int]]:
    atom, _, image = node.partition("@")
    i, j, k = (int(x) for x in image.split(","))
    return int(atom), (i, j, k)


def edge_id(edge_index: int, image: Sequence[int]) -> str:
    """The GRAPH edge ID of a lifted edge: the record's edge ``edge_index`` from its ``i`` atom at ``image``."""
    return f"e{edge_index}@{image[0]},{image[1]},{image[2]}"


def roots_of(state: Mapping, roots: Sequence | None = None) -> tuple[list[tuple[int, tuple[int, int, int]]], str]:
    """The context's roots as (atom, image), sorted and unique, and the policy that chose them: STATES's rule, the
    substrate incidences of the deposited adsorption contacts unless roots are given (an index, or ``{"atom",
    "image"}``). A root is a substrate atom; an image moves only along periodic directions."""
    atoms, pbc = state["atoms"], state["pbc"]
    if roots is None:
        given = [site for contact in state["contacts"] for site in contact["sites"]
                 if atoms[site["atom"]]["role"] == "substrate"]
        policy = "deposited_contact_substrate_incidences"
    else:
        given, policy = list(roots), "explicit"
    out = set()
    for root in given:
        if isinstance(root, Mapping):
            index, image = root["atom"], tuple(int(x) for x in root.get("image", ZERO))
        else:
            index, image = root, ZERO
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(atoms):
            raise Unsupported(f"root atom {index!r} is out of range")
        if atoms[index]["role"] != "substrate":
            raise Unsupported(f"root atom {index} is not a substrate atom")
        if len(image) != 3 or any(x and not periodic for x, periodic in zip(image, pbc)):
            raise Unsupported(f"root image {list(image)} moves along a nonperiodic direction")
        out.add((index, image))
    if not out:
        raise Unsupported("no substrate roots: give roots explicitly or declare adsorption contacts")
    return sorted(out), policy


def lifted_projection(state: Mapping, roots: list[tuple[int, tuple[int, int, int]]], shells: int,
                      bindings: Mapping[str, str] | None = None) -> tuple[Projection, dict]:
    """The substrate graph lifted to a box of images around the roots: per periodic axis, the roots' range widened by
    ``shells`` times the largest neighbor shift along it, and once more by the largest shift of any substrate edge
    (the far end of a port). Nodes are the substrate
    atom images, typed by element; edges are the lifted copies of every substrate-substrate edge with both ends in
    the box, their relation the edge kind. Returns the projection and the lift (box, records) for checking."""
    atoms, pbc = state["atoms"], state["pbc"]
    substrate = [i for i, a in enumerate(atoms) if a["role"] == "substrate"]
    inside = set(substrate)
    edges = [(n, e) for n, e in enumerate(state["edges"]) if e["i"] in inside and e["j"] in inside]
    def widest(axis: int, kinds: set | None) -> int:
        return max((abs(e["shift"][axis]) for _, e in edges if kinds is None or e["kind"] in kinds), default=0)
    # a walk of `shells` neighbor hops, then one edge of any kind out to a port's far end
    reach = [shells * widest(axis, {NEIGHBOR}) + widest(axis, None) if pbc[axis] else 0 for axis in range(3)]
    box = [(min(img[axis] for _, img in roots) - reach[axis], max(img[axis] for _, img in roots) + reach[axis])
           for axis in range(3)]
    images = list(product(*(range(lo, hi + 1) for lo, hi in box)))
    in_box = set(images)
    nodes = [{"id": image_id(a, img), "types": [f"element:{atoms[a]['element']}"]} for img in images for a in substrate]
    lifted = []
    for n, e in edges:
        shift = tuple(e["shift"])
        for img in images:
            far = (img[0] + shift[0], img[1] + shift[1], img[2] + shift[2])
            if far in in_box:
                lifted.append({"id": edge_id(n, img), "source": image_id(e["i"], img), "target": image_id(e["j"], far),
                               "relation": e["kind"]})
    kinds = sorted({e["kind"] for _, e in edges} | {NEIGHBOR})
    if bindings is None:
        bindings = bindings_of(state, box)
    p = Projection.build(nodes=nodes, edges=lifted, relations=kinds, bindings=bindings)
    return p, {"box": box, "nodes": nodes, "edges": lifted}


def bindings_of(state: Mapping, box: Sequence[tuple[int, int]]) -> dict[str, str]:
    """What a lift describes: the deposited record (its STATES record id), its schema, and the box of images lifted."""
    from states.identity import record_id
    return {"source": f"states record {record_id(state)}", "schema": str(state.get("schema")),
            "policy": "substrate atom images; box " + " ".join(f"{lo}..{hi}" for lo, hi in box)}


def context(state: Mapping, roots: Sequence | None = None, shells: int = 2, max_atoms: int = 1000) -> dict:
    """STATES's context extraction answered by GRAPH: every substrate atom image within ``shells`` neighbor hops of
    the roots (with its shell and its witness), the complete relations among them (``edges``) and the relations that
    leave them (``ports``). ``max_atoms`` bounds the atom images, roots included: a context that would exceed it is
    reported ``interrupted`` (STATES raises), never truncated into a complete-looking one."""
    if isinstance(shells, bool) or not isinstance(shells, int) or shells < 0:
        raise Unsupported("shells is a non-negative integer")
    if isinstance(max_atoms, bool) or not isinstance(max_atoms, int) or max_atoms < 1:
        raise Unsupported("max_atoms is a positive integer")
    refs, policy = roots_of(state, roots)
    p, lift = lifted_projection(state, refs, shells)
    res: Result = execute(p, p.request("reachability", sources=[image_id(a, img) for a, img in refs], scope=SCOPE,
                                       max_depth=shells, limits={"max_results": max_atoms}))
    out = {"status": res.status, "complete": res.complete, "result_id": res.result_id, "projection": p.identity,
           "root_policy": policy, "roots": [{"atom": a, "image": list(img)} for a, img in refs],
           "requested_shells": shells, "box": lift["box"]}
    if res.answer is None:
        return {**out, "error": dict(res.error)}
    reached = {}
    for e in res.answer["reached"]:
        atom, image = parse_image_id(e["node"])
        via = None
        if e["via"] is not None:
            index, base = e["via"]["edge"][1:].partition("@")[0], e["via"]["edge"].partition("@")[2]
            via = {"source_edge": int(index), "edge_image": [int(x) for x in base.split(",")],
                   "direction": e["via"]["direction"], "from": list(parse_image_id(e["via"]["from"]))}
        reached[e["node"]] = {"atom": atom, "image": list(image), "shell": e["depth"], "via": via}
    out["nodes"] = sorted(reached.values(), key=lambda n: (n["shell"], n["atom"], n["image"]))
    if res.status != COMPLETED:          # an interrupted context lists what it holds, and claims no relations
        return {**out, "interruption": dict(res.interruption)}
    edges, ports = [], []
    for e in lift["edges"]:
        a, b = e["source"] in reached, e["target"] in reached
        index = int(e["id"][1:].partition("@")[0])
        ends = (parse_image_id(e["source"]), parse_image_id(e["target"]))
        if a and b:
            edges.append({"source_edge": index, "ends": sorted([[x[0], list(x[1])] for x in ends]), "kind": e["relation"]})
        elif a or b:
            inner, outer = ends if a else ends[::-1]
            ports.append({"source_edge": index, "inside": [inner[0], list(inner[1])],
                          "outside": [outer[0], list(outer[1])], "kind": e["relation"]})
    out["edges"] = sorted(edges, key=lambda x: (x["source_edge"], x["ends"]))
    out["ports"] = sorted(ports, key=lambda x: (x["source_edge"], x["inside"], x["outside"]))
    return out
