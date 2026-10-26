# STATES integration

STATES is GRAPH's second consumer. This document records the baseline the integration was built against, the
adapter, the workflow it runs, and its parity with STATES's own behaviour. Nothing in STATES was changed. The adapter
only reads STATES records, and the integration tests write nothing into the checkout.

## Baseline (recorded 25 October 2026)

| What | Observed |
| --- | --- |
| Checkout | `../STATES`, branch `main`, HEAD `f76b96c0d5541c2d2b4eeea4aa1ca7a88cc302b6` (2026-10-25, "Update two tests to intended round-23 behaviour"), STATES 0.4.0, Python 3.12.13, ASE 3.29.0 |
| Local changes | Another session's work in progress: 9 modified files (`compute`, `contract`, `particles`, `placement`, `recognize`, `sketch` and three tests), plus new untracked tests during the run. None of the modules the workflow uses is among them: `context`, `model`, `io`, `atomio`, `identity` and `codec` are unchanged from HEAD |
| Workflow | `states.context.extract_context(state, roots=None, shells=2, max_atoms=1000)`, schema `STATES-CONTEXT/0.4` |

## The workflow: a context of a binding site

STATES records an atomistic catalyst as atoms with roles, periodic edges (with integer image shifts) and adsorption
contacts. A context is a finite, auditable projection of the infinite periodic substrate:

- **Roots.** By default, the substrate incidences of the deposited adsorption contacts; or explicit atom images.
- **Atom images.** Every substrate atom image within `shells` hops of undirected `neighbor` edges between
  substrate atoms.
- **Relations.** The complete relations among those images, of every edge kind, and boundary ports for the
  relations that leave them.
- **Limit.** `max_atoms` bounds the images, roots included. STATES refuses a context that would exceed it, rather
  than truncating a shell.

This is bounded reachability with an output limit: GRAPH's `reachability` with `max_depth = shells` and
`max_results = max_atoms`. GRAPH's answer differs only in how an exceeded limit is reported. GRAPH returns
`interrupted` with what it found; STATES raises. Neither presents a truncated context as complete.

## The adapter

`integrations/states/states_graph_adapter.py` is application-owned. It reads STATES records so that the GRAPH runtime
never does. It is staged here until STATES takes it in.

| GRAPH | From the STATES record |
| --- | --- |
| node | a substrate atom image `<atom>@<i>,<j>,<k>`, typed by its element |
| edge | a lifted copy `e<edge>@<image>` of every substrate-substrate edge, from its `i` atom at that image to its `j` atom at the image plus the edge's shift; its relation is the edge kind |
| scope | `neighbor`, in both directions (STATES's undirected distance relation) |
| the box | per periodic axis, the roots' images widened by `shells` times the largest neighbor shift along that axis, plus the largest shift of any substrate edge (the far end of a port). A walk of `shells` hops cannot leave it |
| bindings | the record (its STATES record id), its schema, and the box lifted |

The periodic graph is infinite; the box makes it finite without losing anything the context can reach.
`context()` turns GRAPH's answer back into STATES terms:

- each atom image with its shell, and its witness (the STATES edge followed, at which image, and in which direction)
- the relations among the reached images
- the ports

## Parity

For every record, root set and shell count (0 to 3), STATES's `extract_context` and the adapter must give:

- the same atom images at the same shells
- the same relations
- the same ports
- the same root policy

Two `max_atoms` values are also checked. The exact size fits for both. One less makes STATES raise and GRAPH report
`interrupted` (`output_limit`), listing only images of the full context at their right shells. Every witness is a
STATES `neighbor` edge followed between the two images it joins, and `verify_result` checks the lifted result.

| Corpus | Records | Contexts compared | Atom images | Relations and ports | Limit checks | Witnesses |
| --- | --- | --- | --- | --- | --- | --- |
| The committed example records (`examples/data`, `examples/demo_output`) | 18 | 176 | 8,736 | 52,776 | 153 | 8,448 |
| Structures built in memory with ASE and STATES's own importer (`from_ase`) | 52 | 584 | 29,297 | 191,093 | 526 | 28,233 |

The two corpora vary the structures in several ways:

- **Root sets.** Default contact roots, an explicit root, and explicit roots moved to another image along every
  periodic direction.
- **Built slabs.** fcc(111), fcc(100), fcc(110), hcp(0001) and bcc(110), in two sizes, with O, H and CO at several
  sites.
- **Small bulk cells.** Their atoms neighbor their own images: 19 such self-image edges, the sharpest case for the
  lift.

All compared contexts agreed.

## Cost

GRAPH answers each context in about 0.12 ms, but the adapter first lifts a whole box of periodic images. That box is
(2·shells + 1) images along every periodic axis, and for large three-dimensional cells it far exceeds the context it
serves (`benchmarks/consumers.py`):

| Contexts | STATES's `extract_context` | GRAPH: lift | GRAPH: query |
| --- | --- | --- | --- |
| Mean of 42 contexts | 15.3 ms | 52 ms | 0.12 ms |
| Smallest lift (450 nodes) | 2.6 ms | 2.1 ms | 0.05 ms |
| Largest lift (58,320 nodes for a 140-image context) | 26.5 ms | 724 ms | 0.24 ms |

A lift pays off when it is reused, for every shell count or root set of a record. For one-off contexts, the
implementation plan proposes a periodic projection that expands images only as a traversal reaches them.

## What stays with STATES

GRAPH reproduces none of STATES's definitions:

- what a context means (identity, its schema, binding maps, its scope statements)
- roles and contacts
- the neighbor rule that made the edges
- how equivalence between contexts is decided (colored multigraph isomorphism)

## Running the tests

```bash
PYTHONDONTWRITEBYTECODE=1 ../STATES/.venv/bin/python -m pytest integrations/states -p no:cacheprovider -s
```

The tests run in STATES's environment, because they import `states` and ASE. They take about two and a half minutes.
