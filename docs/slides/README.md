# GRAPH slides

`docs/GRAPH_slides.pptx` is generated from this folder. Edit the sources here, rebuild, and commit the deck and its
sources together. Do not edit the `.pptx` by hand: the next build overwrites it.

| File | What it holds |
| --- | --- |
| `content.js` | Everything the slides say: the operation families, results, verification counts, timeline, roadmap. Edit this first |
| `build_deck.js` | The theme, the three layouts (`GRAPH_TITLE`, `GRAPH_CONTENT`, `GRAPH_DARK`), and one function per slide, in order, listed in `SLIDES` at the bottom |

## Rebuild

The generator uses [pptxgenjs](https://gitbrains.github.io/PptxGenJS/) with Node.js. Install its packages once,
outside the repository, then point `NODE_PATH` at them:

```bash
npm install --prefix ~/.cache/graph-slides pptxgenjs@3.12.0 react@18.3.1 react-dom@18.3.1 react-icons@5.3.0 sharp@0.33.5
NODE_PATH=~/.cache/graph-slides/node_modules node docs/slides/build_deck.js      # writes docs/GRAPH_slides.pptx
NODE_PATH=~/.cache/graph-slides/node_modules node docs/slides/build_deck.js /tmp/try.pptx   # or anywhere else
```

The build prints a warning for every text it estimates may not fit its box. Check those slides in a render:

```bash
soffice --headless --convert-to pdf --outdir /tmp/graph-slides docs/GRAPH_slides.pptx
pdftoppm -jpeg -r 80 /tmp/graph-slides/GRAPH_slides.pdf /tmp/graph-slides/slide
```

## Where to add things

Every number and claim must come from the repository (README, `docs/API_CONTRACT.md`, the other docs, the git log or a
benchmark run), and each slide's speaker notes name their sources. Keep body text at 14 pt or more: shorten it
rather than shrink it.

- **A new operation.** Add it to a family's `ops` in `content.js` (`name`, `title`, `theory`, `algorithm` with its
  reference, `proofLabel` and `proof`). A family slide holds two or three operations; for more, add another family
  entry (it becomes a new slide, in the order of `families`). A family of one operation can carry a picture beside
  it: set `aside` to a function in `ASIDES` in `build_deck.js` (`rankingSchematic`, `dominatorSchematic`,
  `incidenceExample`). Then:
  - raise `meta.operations` (the overview title, the architecture slide and the closing slide use it)
  - add the name to its card in `overview` (six cards fill the 3 × 2 grid: join a related family, as max_flow and
    matching and dominators joined "Structure and flows", rather than add a seventh)
  - set `isNew: true` while it is implemented but not committed, and remove it once it is
  - move it out of `roadmap` if it was listed there
- **A new measured result.** A comparison with networkx goes into `networkxChart` (one category per operation, GRAPH
  and networkx seconds side by side; say the graph sizes in its `caption`). Otherwise replace one of the six
  `speedTiles` (the grid holds six), or add a chart slide modeled on `headToHeadSlide` and list it in `SLIDES`. Say
  when the machine was loaded; give ratios where absolute times are unreliable.
- **New verification counts** (tests, conformance cases, parity tests): `verification`; review notes: `reviews`.
- **A new increment in the history.** Add a row to `timeline` (15 rows fill the column; merge older rows to make
  room) and move `latest: true` to it; add its conformance count to `growth`, and update `growth.tests`,
  `growth.operations` and `timelineLede`.
- **The roadmap.** `roadmapLede`, `roadmap` (up to six rows) and `boundaries`, from the latest plan or survey.
- **Version, date and contract.** `meta`.
