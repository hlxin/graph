# Performance

Every result carries its `timing` (bind, search, witness, result), left out of its `result_id`. The benchmarks
measure whole requests: building the projection, execution, witness reconstruction and serialization, with the
workload's identity, the versions and raw results (written to `benchmarks/runs/`, which git ignores).

```bash
python3 benchmarks/bench.py --sizes small,medium            # every phase on random multigraphs, parity with NetworkX
python3 benchmarks/revalidation.py --nodes 10000 --edges 50000   # revalidating cached results against rerunning them
PYTHONDONTWRITEBYTECODE=1 ../scale/.venv/bin/python benchmarks/scale_query.py      # GRAPH against SCALE's query engine
PYTHONDONTWRITEBYTECODE=1 ../STATES/.venv/bin/python benchmarks/consumers.py states # GRAPH against STATES's contexts
```

Absolute times depend on the machine and its load; compare columns of the same run.

## Against SCALE's query engine

`benchmarks/scale_query.py` runs SCALE's own engine (`pgraph_query.run` on each plan's structural form, with its
index built once per document) against GRAPH's `pattern` operation through the SCALE adapter (the projection built
once). It asserts the same rows in the same order on every query, and times GRAPH both listing the rows and counting
them without listing. Each time is the best of three runs. Run of 28 October 2026 (Python 3.11, Apple silicon, load
average 6 to 9); GRAPH was never slower on any query:

| Workload | Queries | Rows | SCALE | GRAPH, listing | GRAPH, counting |
| --- | --- | --- | --- | --- | --- |
| Competency questions (edge-step plans, on their documents) | 39 | 203 | 50 ms | 6.8 ms (7.3×) | 5.6 ms (8.9×) |
| Methanol synthesis, public example (2,851 nodes, 9,335 links) | 1,052 | 128,146 | 9.53 s | 0.95 s (10.0×) | 0.50 s (18.9×) |
| Methanol synthesis, full pre-1966 graph (4,866 nodes, 16,584 links) | 1,714 | 261,709 | 23.2 s | 2.10 s (11.0×) | 1.11 s (20.9×) |
| The public example ×16 (45,616 nodes, 149,360 links) | 1,052 | 2,050,336 | 178.6 s | 16.8 s (10.7×) | 6.10 s (29.3×) |

The largest answer, 167,520 rows on the ×16 graph, takes 13.7 s in SCALE, 0.60 s listed by GRAPH and 12.8 ms counted.
What each builds first: SCALE's index takes 3.7 ms on the public example and 68 ms on the ×16 graph; GRAPH's
projection, which also computes its identity digest, takes 120 ms and 2.27 s. At the average saving per query, the
projection pays for itself after about fourteen queries.

Where the difference comes from:

- **Built once.** The projection holds integer IDs, adjacency in canonical order, each node's types (with their
  ancestors) as a bitmask and each relation's specializations, and keeps per-scope edge lists across queries. Nothing
  is sorted or indexed per query.
- **Each node expanded once.** Tree patterns are matched by semi-join reduction: each step scans each candidate's
  hops once, whatever the number of partial rows that reach it, and dead ends are pruned before any row is listed.
- **Counting without listing.** The number of rows is a sum of products over the kept hops, so it costs what the
  pruning costs, not what the rows cost. This is the part of the gain that grows with the size of the answer.
- **Frozen results.** A result is immutable, so nothing is copied to protect it.

SCALE's engine also does work GRAPH leaves to the adapter: it labels every row with its evidence and its sources'
retraction status, and copies its answer before returning it. On the largest answer of the public example, the copy
is about two thirds of SCALE's time.

## Against STATES's context extraction

On 42 contexts of STATES's example records, GRAPH on the periodic cell is 3.4 times faster than STATES's own
`extract_context` on average, and 18 times faster than through a lifted box of images
([STATES_INTEGRATION.md](STATES_INTEGRATION.md)).
