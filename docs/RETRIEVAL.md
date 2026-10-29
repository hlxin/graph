# Retrieval: search, then traverse

`graph_runtime.retrieval` is SCALE's evidence search (`tools/passages.py`) made generic. It ranks text items that
name a projection's records. Its hits name the nodes where a traversal starts, and a traversal's records can in turn
restrict a search. It is optional: it needs numpy (`pip install 'graph-runtime-sci[retrieval]'`), and nothing
else in GRAPH imports it.

Relevance is a heuristic ranking, not evidence. What is exact is the computation: the same items, query and settings
give the same hits, in the same order, with the same scores, on every machine. There is no approximate
nearest-neighbour search, sampling or learned index.

## Parts

| Part | What it is |
| --- | --- |
| `Tokenizer(aliases=, variants=, word=)` | Lower-cased words. An application adds its vocabulary: `aliases` are case-sensitive whole words with text added after them (`{"Cu": "copper"}`; CO and Co stay apart), and `variants` are lower-cased words with words added after them (`{"sulphur": "sulfur"}`). The default word is any run of letters and digits in any script, with a decimal part. `id` is the digest of the settings |
| `HashEmbedder(tokenizer, dim=512)` | Vectors with no model to download, the same on every machine. The features are each word (weight 1), its character 4-grams (0.5 each) and each pair of adjacent words (1). They are hashed (BLAKE2b) into `dim` signed dimensions, with weight 1 + log of a count of at least 1. Any object with an `id` and `encode(texts)` can embed instead, for example a neural sentence model |
| `Index.build(items, embedder=, tokenizer=)` | The items (`{id, text, record?, ...}`, unique ids), their int8 vectors (each row L2-normalized, then scaled to its largest entry), and their BM25 statistics as an inverted index |
| `index.search(query, k=, mode=, records=, where=)` | The best `k` items, each a copy of the item with `score`, `keyword` (BM25) and `cosine`. A score the mode does not compute is `None` |
| `records(hits)`, `seeds(hits, projection)` | The records the hits name, each once, best first; `seeds` keeps those that are nodes of the projection, ready to be the sources of a traversal |
| `split_text(text, limit=900)` | Passages of a long text: paragraphs joined up to the limit, and a longer paragraph cut at a sentence end |

## Modes

| Mode | Score |
| --- | --- |
| `hybrid` | the mean of BM25 scaled to its best over all items and the cosine (a negative cosine counts as 0) |
| `vector` | the cosine of the int8 vectors |
| `keyword` | BM25 (k1 1.2, b 0.75) |
| `rrf` | reciprocal rank fusion of the BM25 and cosine rankings, each read to 1,000 items (or twice `k`): the sum of 1 / (60 + rank), at most 2 / 61 |

Only items of positive score are hits. They come best first, with ties broken by item id. The ranking is exactly the
head of a full sort: every item tied with the k-th best is a candidate before the candidates are sorted.

`records` keeps the items whose record is one of them. `where` keeps the items it is true of. With either, `rrf`
fuses the rankings of the kept items alone, so items left out never crowd out the ones kept (an eligible match past
the fusion depth of all items is still found). In the other modes an item's score does not depend on the filter.
`records` is declarative: unlike a function, it can be recorded with the search it restricted.

## Exactness and cost

A search reads each item once:

- BM25 reads only the postings of the query's words.
- The cosine is the product of int8 vectors summed in float32. This is exact while every partial sum stays below
  2**24, which holds up to 1,040 dimensions; above that, float64 is used. Norms are computed once. The float32
  vectors are kept for the next queries when they fit in 2 GiB; otherwise they are converted in blocks of 65,536
  rows.
- Only the best candidates are sorted.

The tests compare every ranking with the plain definitions: BM25 and the cosine of every item, then a full sort.

Building an index tokenizes and encodes each distinct text once. On SCALE's public methanol-synthesis example
(12,802 evidence quotes, 3,594 distinct texts) it takes 0.38 s, against 0.93 s for SCALE's own index. Searches take
0.1 ms (keyword) to 0.7 ms (rrf) per query, the same as SCALE's.

## Identity and saved indexes

`index_id` is the digest of the items, the embedder id, the tokenizer id and the vectors. Bind it into a projection
(`bindings={"evidence_index": ix.index_id}`): every result then names the index it was made with, and
`stale_bindings` reports a changed index.

`index.save(stem)` writes three files. `Index.load(stem)` reads them back:

| File | Holds | Tied to |
| --- | --- | --- |
| `<stem>.jsonl` | a header (embedder, tokenizer settings, counts, digests) and one item per line | the header records the items' digest |
| `<stem>.i8` | the int8 vectors | the header records their digest and the items' |
| `<stem>.terms` | the inverted index: the words, then the offsets, postings, counts and lengths | it records the items' digest, the tokenizer id, and a digest of its words and arrays together |

A part that does not match the items is not used. Vectors are rebuilt with the embedder, which is rebuilt from the
saved settings for a hash embedder and must be passed for any other. If the embedder is missing, the load is refused
(`stale_vectors`). An inverted index is rebuilt from the items. `index.rebuilt` names what was rebuilt. Unlike
SCALE's index, an edited items file makes the vectors stale too, not only the inverted index.

## What differs from SCALE's `passages.py`

GRAPH reproduces SCALE's search exactly when it is given SCALE's words (the adapter's `tokenizer()`), and it adds
these:

- The vocabulary (formula aliases, spellings, the word pattern) is the application's, passed as settings. SCALE's
  chemistry is in SCALE's adapter, not in GRAPH.
- An index records its tokenizer. A saved inverted index made with another tokenizer is rebuilt.
- `index_id`, a `records` filter, and `None` for a score that was not computed. SCALE reports 0 and rounds scores to
  four places; GRAPH keeps the exact floats, and the adapter rounds.
- Vectors saved for other items are rebuilt or refused, never used.
- Each distinct text is encoded once.

What stays with SCALE: which items a document has (its evidence quotes and their positions), who may read them
(access), the corpus's page texts, and what a hit means for an answer.

## Parity with SCALE

On SCALE's public methanol-synthesis example (55 public-domain sources, 2,851 nodes, 9,335 links, 12,802 evidence
quotes, 235 page texts), `integrations/scale/test_scale_retrieval_parity.py` checks:

| Check | Result |
| --- | --- |
| Vectors and inverted index | byte for byte SCALE's |
| BM25 and cosine of every item, for 55 queries (15 fixed, 40 cut from quotes) | bit for bit SCALE's |
| Searches: 55 queries × 4 modes × k of 1, 10 and 200 × no filter, a source-year filter and a claim filter (also as `records`) | 1,980 searches, 130,173 hits, all SCALE's, in SCALE's order, with SCALE's scores |
| The manuscript's questions that rank claims by their quotes (q2, q3, q5: match, text stage, `published_before`) | the same claims in the same order with the same text scores (`adapter.text_ranked` on SCALE's rows before the text stage) |
| Page texts | all 235 pages split into SCALE's passages (1,350), indexed and searched alike |
