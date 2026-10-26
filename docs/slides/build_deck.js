// Build docs/GRAPH_slides.pptx from content.js.
//
//   NODE_PATH=<folder with pptxgenjs, react, react-dom, react-icons, sharp>/node_modules node docs/slides/build_deck.js [out.pptx]
//
// See README.md in this folder. The deck is structured: one theme (THEME), three layouts (GRAPH_TITLE,
// GRAPH_CONTENT, GRAPH_DARK) with named placeholders, and one PowerPoint section per part of the talk. Slides are
// listed in order in SLIDES at the bottom; each is a function. Operation families are drawn from content.js by
// familySlide, so a new operation is usually a new entry there, not new code.

"use strict";

const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const Lu = require("react-icons/lu");
const K = require("./content.js");

const OUT = process.argv[2] ? path.resolve(process.argv[2]) : path.join(__dirname, "..", "GRAPH_slides.pptx");

// ------------------------------------------------------------------------------------------------- theme
const THEME = {
  name: "GRAPH",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "1A2421", // ink
    lt1: "FFFFFF",
    dk2: "12332E", // deep pine: title and closing slides
    lt2: "EDF3F1", // pale mint: cards
    accent1: "1E7B6B", // verified teal: the dominant accent
    accent2: "C77C02", // amber: witness paths, bounds, "new"
    accent3: "3D5A80", // slate
    accent4: "A23B53", // berry: "fails"
    accent5: "8FC5B6", // light teal, on dark backgrounds
    accent6: "56626A", // muted text
    hlink: "1E7B6B",
    folHlink: "3D5A80",
  },
};
const HEX = THEME.colors; // for the options that take hex only (charts, icons)
const MONO = "Courier New";
const W = 13.333;
const MX = 0.6; // side margin
const CW = W - 2 * MX; // content width
const TOP = 1.85; // content top on GRAPH_CONTENT
const BOTTOM = 6.8; // content bottom (the footer sits below)

let C; // pres.SchemeColor, set in main()
let pres;
const warnings = [];
const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
  "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"];
const word = (n) => NUMBER_WORDS[n] || String(n);
const Word = (n) => word(n).charAt(0).toUpperCase() + word(n).slice(1);

// ------------------------------------------------------------------------------------------------- helpers
// Inline markup: `code` in the monospace font (a point smaller, as Courier New runs large), **bold** in bold.
function rich(text, base = {}) {
  const out = [];
  for (const part of String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
    if (!part) continue;
    if (part.startsWith("**")) out.push({ text: part.slice(2, -2), options: { ...base, bold: true } });
    else if (part.startsWith("`")) out.push({ text: part.slice(1, -1), options: { ...base, fontFace: MONO, ...(base.fontSize ? { fontSize: base.fontSize - 1 } : {}) } });
    else out.push({ text: part, options: { ...base } });
  }
  return out;
}

// Paragraphs: [{ text, opts }] -> runs, each paragraph ending in a line break.
function paras(list) {
  const out = [];
  list.forEach((p, i) => {
    const runs = rich(p.text, p.opts || {});
    if (i < list.length - 1) runs[runs.length - 1].options.breakLine = true;
    out.push(...runs);
  });
  return out;
}

// A line count for a text at a size in a width, by word wrap with average character widths (Calibri about 0.46 em,
// bold 0.49 em; Courier New 0.6 em, set a point smaller inside `code`). Used to size boxes and to warn when a text
// may overflow; the render is the final check.
function lines(text, widthIn, pt, { mono = false, bold = false } = {}) {
  const maxEm = (widthIn * 72) / pt;
  const reg = bold ? 0.49 : 0.46;
  let n = 0;
  for (const para of String(text).split("\n")) {
    n += 1;
    let cur = 0;
    for (const seg of para.split(/(`[^`]*`)/)) {
      const code = seg.startsWith("`");
      for (const word of seg.replace(/`|\*\*/g, "").split(/\s+/)) {
        if (!word) continue;
        const em = mono ? word.length * 0.6 : code ? (word.length * 0.6 * (pt - 1)) / pt : word.length * reg;
        const space = mono ? 0.6 : 0.25;
        if (cur === 0) cur = em;
        else if (cur + space + em <= maxEm) cur += space + em;
        else { n += 1; cur = em; }
      }
    }
  }
  return n;
}
const height = (text, widthIn, pt, spacing = 1.22, style = {}) => (lines(text, widthIn, pt, style) * pt * spacing) / 72;

function check(what, need, have) {
  if (need > have + 0.01) warnings.push(`${what}: needs about ${need.toFixed(2)} in, has ${have.toFixed(2)} in`);
}

const iconCache = new Map();
async function iconData(name, hex) {
  const key = `${name}:${hex}`;
  if (!iconCache.has(key)) {
    const comp = Lu[name];
    if (!comp) throw new Error(`no icon ${name} in react-icons/lu`);
    const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(comp, { color: `#${hex}`, size: "256" }));
    const png = await sharp(Buffer.from(svg)).resize(256, 256).png().toBuffer();
    iconCache.set(key, `image/png;base64,${png.toString("base64")}`);
  }
  return iconCache.get(key);
}

// The motif: an icon in a filled circle.
async function iconCircle(slide, name, x, y, d, { fill = C.accent1, iconHex = HEX.lt1, label = name } = {}) {
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { type: "none" }, objectName: `icon ${label}` });
  const inset = d * 0.22;
  slide.addImage({ data: await iconData(name, iconHex), x: x + inset, y: y + inset, w: d - 2 * inset, h: d - 2 * inset, altText: label });
}

function card(slide, x, y, w, h, { fill = C.background2, line = null, name = "card" } = {}) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: 0.1, fill: { color: fill }, line: line || { type: "none" }, objectName: name,
  });
}

function text(slide, content, opts) {
  const runs = typeof content === "string" ? rich(content, {}) : content;
  slide.addText(runs, { isTextBox: true, margin: 0, valign: "top", ...opts });
}

function line(slide, x1, y1, x2, y2, opts) {
  slide.addShape(pres.shapes.LINE, {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
    flipH: x2 < x1, flipV: y2 < y1, line: opts,
  });
}

function pill(slide, label, x, y, { fill = C.accent2, w = null } = {}) {
  const width = w || 0.25 + label.length * 0.085;
  slide.addText(label, {
    shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.13, x, y, w: width, h: 0.26, fill: { color: fill },
    color: C.background1, fontSize: 10, bold: true, align: "center", valign: "middle", margin: 0, charSpacing: 0.5,
    objectName: `pill ${label}`,
  });
  return width;
}

function contentSlide(section, title, lede) {
  const s = pres.addSlide({ masterName: "GRAPH_CONTENT", sectionTitle: section });
  s.addText(title, { placeholder: "title" });
  if (lede) s.addText(rich(lede), { placeholder: "lede" });
  return s;
}

// Chart text in the theme's body font, the same size and color on every chart.
const CHART_FONT = "Calibri";
function chartStyle(extra = {}) {
  return {
    catAxisLabelFontFace: CHART_FONT, catAxisLabelFontSize: 12, catAxisLabelColor: HEX.dk1,
    valAxisLabelFontFace: CHART_FONT, valAxisLabelFontSize: 11, valAxisLabelColor: HEX.accent6,
    dataLabelFontFace: CHART_FONT, dataLabelFontSize: 12, dataLabelColor: HEX.dk1,
    titleFontFace: CHART_FONT, titleFontSize: 14, titleColor: HEX.dk1, showTitle: true,
    valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false,
    catAxisLineShow: false, valAxisLineShow: false,
    ...extra,
  };
}

// A small graph with one highlighted witness path: the deck's picture of "an answer with its proof".
function graphMotif(slide, x0, y0, w, h, { scale = 1 } = {}) {
  const P = [[0.10, 0.12], [0.42, 0.05], [0.80, 0.15], [0.24, 0.38], [0.58, 0.33], [0.93, 0.44], [0.07, 0.66],
    [0.40, 0.60], [0.72, 0.68], [0.28, 0.92], [0.60, 0.95], [0.95, 0.86]];
  const E = [[0, 1], [1, 2], [0, 3], [1, 4], [2, 4], [2, 5], [3, 4], [3, 6], [3, 7], [4, 7], [4, 8], [5, 8], [6, 7],
    [6, 9], [7, 9], [7, 10], [8, 10], [8, 11], [5, 11], [9, 10]];
  const pathNodes = [0, 3, 4, 8, 11];
  const onPath = (a, b) => pathNodes.some((n, i) => i > 0 && ((pathNodes[i - 1] === a && n === b) || (pathNodes[i - 1] === b && n === a)));
  const at = (i) => [x0 + P[i][0] * w, y0 + P[i][1] * h];
  for (const [a, b] of E) {
    if (onPath(a, b)) continue;
    const [x1, y1] = at(a);
    const [x2, y2] = at(b);
    line(slide, x1, y1, x2, y2, { color: C.accent5, width: 1.25 * scale });
  }
  for (let i = 1; i < pathNodes.length; i++) {
    const [x1, y1] = at(pathNodes[i - 1]);
    const [x2, y2] = at(pathNodes[i]);
    line(slide, x1, y1, x2, y2, { color: C.accent2, width: 3.5 * scale });
  }
  P.forEach((_, i) => {
    const big = pathNodes.includes(i);
    const d = (big ? 0.3 : 0.2) * scale;
    const [cx, cy] = at(i);
    slide.addShape(pres.shapes.OVAL, {
      x: cx - d / 2, y: cy - d / 2, w: d, h: d, fill: { color: big ? C.accent2 : C.accent5 },
      line: { color: C.text2, width: 1.5 }, objectName: big ? "witness node" : "node",
    });
  });
}

// ------------------------------------------------------------------------------------------------- layouts
function defineLayouts() {
  pres.defineSlideMaster({
    title: "GRAPH_TITLE",
    background: { color: C.text2 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 1.3, w: 7.2, h: 1.3, fontSize: 72, bold: true, color: C.background1, valign: "bottom", align: "left", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "subtitle", type: "body", x: 0.8, y: 2.75, w: 7.0, h: 1.9, fontSize: 24, color: C.background1, valign: "top", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "meta", type: "body", x: 0.8, y: 5.45, w: 7.0, h: 0.9, fontSize: 14, color: C.accent5, valign: "top", margin: 0 }, text: "" } },
    ],
  });
  pres.defineSlideMaster({
    title: "GRAPH_CONTENT",
    background: { color: C.background1 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: MX, y: 0.4, w: CW, h: 0.75, fontSize: 34, bold: true, color: C.text1, valign: "bottom", align: "left", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "lede", type: "body", x: MX, y: 1.2, w: CW, h: 0.45, fontSize: 16, color: C.accent6, valign: "top", margin: 0 }, text: "" } },
      { text: { text: `GRAPH · ${K.meta.contract} · ${K.meta.date}`, options: { x: MX, y: 7.02, w: 6, h: 0.3, fontSize: 10, color: C.accent6, margin: 0, valign: "middle" } } },
    ],
    slideNumber: { x: W - MX - 0.6, y: 7.02, w: 0.6, h: 0.3, fontSize: 10, color: HEX.accent6, align: "right" },
  });
  pres.defineSlideMaster({
    title: "GRAPH_DARK",
    background: { color: C.text2 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 0.9, w: 7.6, h: 1.6, fontSize: 36, bold: true, color: C.background1, valign: "bottom", align: "left", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 2.85, w: 7.4, h: 3.0, fontSize: 18, color: C.background1, valign: "top", margin: 0 }, text: "" } },
    ],
    slideNumber: { x: W - MX - 0.6, y: 7.02, w: 0.6, h: 0.3, fontSize: 10, color: HEX.accent5, align: "right" },
  });
}

// ------------------------------------------------------------------------------------------------- slides
async function titleSlide() {
  pres.addSection({ title: "Why GRAPH" });
  const s = pres.addSlide({ masterName: "GRAPH_TITLE", sectionTitle: "Why GRAPH" });
  s.addText(K.meta.title, { placeholder: "title" });
  s.addText([
    { text: K.meta.expansion, options: { fontSize: 24, color: C.background1, breakLine: true, paraSpaceAfter: 14 } },
    { text: K.meta.tagline, options: { fontSize: 18, color: C.accent5 } },
  ], { placeholder: "subtitle" });
  s.addText([
    { text: K.meta.author, options: { breakLine: true } },
    { text: `${K.meta.date} · version ${K.meta.version} · contract ${K.meta.contract}` },
  ], { placeholder: "meta" });
  graphMotif(s, 8.55, 1.0, 4.0, 5.4);
  s.addNotes("GRAPH: Graph Runtime for Analysis, Provenance, Hypotheses. A small Python library (standard library only) of exact graph operations with witnesses, shared by the group's projects. Status: in development, nothing published (README.md). The highlighted path in the picture is the deck's motif: an answer together with the edges that prove it.");
}

async function motivationSlide() {
  const s = contentSlide("Why GRAPH", "One graph library for the group's projects",
    "Every project asks graph questions. GRAPH answers them once, exactly and with proof; each application keeps its science.");
  const items = [
    ["The questions are graph questions.", "Which evidence supports a claim; what rests on a retraction; which atoms surround a binding site; which route makes a product."],
    ["Each project had its own graph code.", "SCALE's query engine, lifecycle and passage search; STATES's context extraction."],
    ["One library, one contract.", "GRAPH takes over the generic parts, with parity tests against each application's own code, and aims to be the graph library every graph-related project uses."],
  ];
  let y = TOP + 0.05;
  for (const [head, body] of items) {
    const h = 0.32 + height(body, 5.0, 15) + 0.05;
    text(s, [
      { text: head, options: { bold: true, fontSize: 17, color: C.accent1, breakLine: true, paraSpaceAfter: 4 } },
      { text: body, options: { fontSize: 15, color: C.text1 } },
    ], { x: MX, y, w: 5.0, h, objectName: head });
    y += h + 0.35;
  }
  check("motivation text", y - 0.35, BOTTOM);

  // Hub: GRAPH and its consumers.
  const cx = 9.37, cy = 4.33, d = 1.5;
  const cards = [
    { x: 6.05, y: TOP, name: "SCALE", what: "Scientific claims and evidence", uses: "typed paths, impact, patterns, support, retrieval", status: "parity-tested", fill: C.accent1 },
    { x: 9.68, y: TOP, name: "STATES", what: "Atomistic structures, crystal graphs", uses: "periodic reachability, periodicity", status: "parity-tested", fill: C.accent1 },
    { x: 6.05, y: 5.3, name: "RATES", what: "Reaction kinetics", uses: "derivation, bottleneck paths", status: "planned", fill: C.accent6 },
    { x: 9.68, y: 5.3, name: "Cathanor", what: "Catalysis knowledge", uses: null, status: "planned", fill: C.accent6 },
  ];
  const cw = 3.05, ch = 1.5;
  for (const c of cards) line(s, cx, cy, c.x + cw / 2, c.y + ch / 2, { color: C.accent5, width: 2 });
  for (const c of cards) {
    card(s, c.x, c.y, cw, ch, { name: `consumer ${c.name}` });
    text(s, c.name, { x: c.x + 0.2, y: c.y + 0.14, w: 1.6, h: 0.32, fontSize: 17, bold: true, color: C.text1 });
    pill(s, c.status, c.x + cw - 0.2 - (0.25 + c.status.length * 0.085), c.y + 0.17, { fill: c.fill });
    const body = [{ text: c.what, options: { fontSize: 14, color: C.text1, breakLine: !!c.uses } }];
    if (c.uses) body.push({ text: c.uses, options: { fontSize: 13, color: C.accent6, italic: true } });
    text(s, body, { x: c.x + 0.2, y: c.y + 0.52, w: cw - 0.4, h: ch - 0.62 });
  }
  s.addShape(pres.shapes.OVAL, { x: cx - d / 2, y: cy - d / 2, w: d, h: d, fill: { color: C.text2 }, line: { color: C.accent2, width: 3 }, objectName: "GRAPH hub" });
  text(s, "GRAPH", { x: cx - d / 2, y: cy - 0.25, w: d, h: 0.5, fontSize: 22, bold: true, color: C.background1, align: "center", valign: "middle", fontFace: "Cambria" });
  s.addNotes("Sources: README.md (applications keep their scientific definitions; each reaches GRAPH through an adapter it owns), docs/SCALE_INTEGRATION.md (SCALE's tools/pgraph_query.py, lifecycle.py, passages.py), docs/STATES_INTEGRATION.md (extract_context). SCALE and STATES adapters are staged in integrations/ with parity tests. RATES and Cathanor have no adapter yet; derivation and bottleneck paths are phrased in reaction-network terms in docs/API_CONTRACT.md.");
}

async function principlesSlide() {
  const s = contentSlide("Why GRAPH", "Five design principles", "Exact answers, with their proof, within a budget, the same every time.");
  // The statement panel.
  card(s, MX, TOP, 4.1, BOTTOM - TOP, { fill: C.text2, name: "principle quote" });
  text(s, [
    { text: "“", options: { fontSize: 60, color: C.accent2, fontFace: "Cambria", breakLine: true } },
    { text: "An interrupted result never certifies a negative.", options: { fontSize: 26, color: C.background1, fontFace: "Cambria", breakLine: true, paraSpaceAfter: 18 } },
    { text: "found: false, complete: true and reachable(x) is False happen only when the search finished.", options: { fontSize: 14, color: C.accent5, breakLine: true, paraSpaceAfter: 10 } },
    { text: "CLAUDE.md, invariants", options: { fontSize: 12, color: C.accent5, italic: true } },
  ], { x: MX + 0.35, y: TOP + 0.2, w: 3.4, h: BOTTOM - TOP - 0.4, valign: "middle" });

  const rows = [
    ["LuTarget", "Exact only", "No approximate modes or sampling: integer and exact rational arithmetic; PageRank as proven intervals."],
    ["LuShieldCheck", "Witnesses and certificates", "Every positive names the records that prove it; on request, certificates prove negatives, optima and bounds."],
    ["LuTimer", "Bounded, interruptible work", "Expansion budget, output limit, deadline, cancellation. What is unknown is reported as unknown (None), never as False."],
    ["LuFingerprint", "Reproducible", "Immutable projections with identity digests, normalized requests, canonical order: the same projection and request give the same result_id."],
    ["LuPuzzle", "Adapters own the science", "Standard library only; GRAPH imports no application vocabulary. Each application reaches it through an adapter it owns."],
  ];
  const x0 = MX + 4.5, w = CW - 4.5, rh = (BOTTOM - TOP) / rows.length;
  for (let i = 0; i < rows.length; i++) {
    const [ic, head, body] = rows[i];
    const y = TOP + i * rh;
    await iconCircle(s, ic, x0, y + 0.08, 0.62, { label: head });
    const tw = w - 0.9;
    check(`principle ${head}`, 0.3 + height(body, tw, 14), rh - 0.05);
    text(s, [
      { text: head, options: { fontSize: 17, bold: true, color: C.text1, breakLine: true, paraSpaceAfter: 2 } },
      { text: body, options: { fontSize: 14, color: C.text1 } },
    ], { x: x0 + 0.9, y: y + 0.04, w: tw, h: rh - 0.05 });
  }
  s.addNotes("Sources: README.md (explicit inputs, exact only, bounded work, reproducible results, witnesses checked against the input records), CLAUDE.md (Invariants). Personalized PageRank is in as proven intervals (docs/API_CONTRACT.md §8).");
}

async function architectureSlide() {
  pres.addSection({ title: "Architecture" });
  const s = contentSlide("Architecture", "Architecture: from records to checked results",
    "Every operation binds an immutable projection and a normalized request, and returns one kind of result.");
  const steps = [
    ["LuPuzzle", "Adapter", "Owned by the application; maps its records to nodes and edges"],
    ["LuLayers", "Projection", "Immutable view, original IDs, hierarchies; identity digest"],
    ["LuFileJson", "Request", "Normalized: explicit scope, defaults and limits; request_id"],
    ["LuCpu", "Engine", `${K.meta.operations} exact operations under budget, deadline, cancellation`],
    ["LuBadgeCheck", "Result", "Status, answer with witnesses, reproducible result_id"],
  ];
  const bw = 2.07, gap = (CW - 5 * bw) / 4, by = TOP, bh = 1.8;
  for (let i = 0; i < steps.length; i++) {
    const [ic, name, body] = steps[i];
    const x = MX + i * (bw + gap);
    card(s, x, by, bw, bh, { name: `pipeline ${name}`, fill: i === 0 ? C.background1 : C.background2, line: i === 0 ? { color: C.accent6, width: 1, dashType: "dash" } : null });
    await iconCircle(s, ic, x + 0.15, by + 0.15, 0.46, { label: name });
    text(s, name, { x: x + 0.7, y: by + 0.15, w: bw - 0.8, h: 0.46, fontSize: 16, bold: true, color: C.text1, valign: "middle" });
    check(`architecture ${name}`, height(body, bw - 0.3, 14), bh - 0.8);
    text(s, body, { x: x + 0.15, y: by + 0.72, w: bw - 0.3, h: bh - 0.8, fontSize: 14, color: C.text1 });
    if (i < steps.length - 1) line(s, x + bw + 0.04, by + bh / 2, x + bw + gap - 0.04, by + bh / 2, { color: C.accent1, width: 2, endArrowType: "triangle" });
  }
  const row2 = [
    ["LuFlaskConical", "Hypotheses", "Candidate records: always unverified, never traversed", false],
    ["LuSearch", "Retrieval (optional)", "Exact text search; its hits name where a traversal starts", false],
    ["LuShieldCheck", "verify_result", "Re-checks witnesses, closures and certificates against the records", true],
    ["LuRefreshCw", "revalidate", "Does a cached result still hold after an edit? holds, fails, unknown", true],
  ];
  const w2 = (CW - 3 * 0.3) / 4, y2 = by + bh + 0.45, h2 = 1.45;
  // Elbow connectors from Result down to the two checks.
  const rx = MX + 4 * (bw + gap) + bw / 2, jy = by + bh + 0.2;
  const targets = row2.map((r, i) => (r[3] ? MX + i * (w2 + 0.3) + w2 / 2 : null)).filter((t) => t !== null);
  line(s, rx, by + bh + 0.02, rx, jy, { color: C.accent1, width: 2 });
  line(s, Math.min(...targets), jy, Math.max(rx, ...targets), jy, { color: C.accent1, width: 2 });
  for (const tx of targets) line(s, tx, jy, tx, y2 - 0.03, { color: C.accent1, width: 2, endArrowType: "triangle" });
  for (let i = 0; i < row2.length; i++) {
    const [ic, name, body, fromResult] = row2[i];
    const x = MX + i * (w2 + 0.3);
    card(s, x, y2, w2, h2, { name: `component ${name}` });
    await iconCircle(s, ic, x + 0.15, y2 + 0.15, 0.42, { fill: fromResult ? C.accent1 : C.accent3, label: name });
    text(s, name, { x: x + 0.67, y: y2 + 0.15, w: w2 - 0.8, h: 0.42, fontSize: 16, bold: true, color: C.text1, valign: "middle", fontFace: name.includes("_") || name === "revalidate" ? MONO : undefined });
    check(`architecture ${name}`, height(body, w2 - 0.3, 14), h2 - 0.7);
    text(s, body, { x: x + 0.15, y: y2 + 0.65, w: w2 - 0.3, h: h2 - 0.7, fontSize: 14, color: C.text1 });
  }
  const y3 = y2 + h2 + 0.3, h3 = BOTTOM - y3;
  card(s, MX, y3, CW, h3, { fill: C.text2, name: "agents" });
  await iconCircle(s, "LuBot", MX + 0.2, y3 + (h3 - 0.46) / 2, 0.46, { fill: C.accent2, label: "agents" });
  text(s, [
    { text: "For agents and programs:  ", options: { bold: true, color: C.background1, fontSize: 16 } },
    ...rich("10 JSON tools · the `graph-runtime` command line · an MCP server over stdio. Results are data; nothing is written.", { color: C.background1, fontSize: 16 }),
  ], { x: MX + 0.85, y: y3, w: CW - 1.0, h: h3, valign: "middle" });
  s.addNotes("Sources: docs/API_CONTRACT.md §1 (projections, identity, hierarchies, periodic projections), §2 (requests, request_id), §3 (execution, limits; execute never raises once its arguments are valid), §5 (results), §6 (verify_result), §7 (revalidation); docs/HYPOTHESES_AND_PROVENANCE.md; docs/RETRIEVAL.md; README.md (tools). The adapter box is dashed: it belongs to the application, not to GRAPH.");
}

async function codeSlide() {
  const s = contentSlide("Architecture", "A request and its result",
    "Plain Python: build a projection from your records, ask, and read an answer that names your IDs.");
  const code = [
    ["from graph_runtime import Projection, execute", ""],
    ["", ""],
    ["p = Projection.build(", ""],
    ["    nodes=[{\"id\": \"test:1\", \"types\": [\"Test\"]},", ""],
    ["           {\"id\": \"sample:1\", \"types\": [\"Sample\"]},", ""],
    ["           \"paper:1\"],", ""],
    ["    edges=[{\"id\": \"link:1\", \"source\": \"test:1\",", ""],
    ["            \"target\": \"sample:1\", \"relation\": \"USES\"},", ""],
    ["           {\"id\": \"link:2\", \"source\": \"sample:1\",", ""],
    ["            \"target\": \"paper:1\", \"relation\": \"REPORTED_IN\"}],", ""],
    ["    bindings={\"source\": \"my-document@sha256:…\",", ""],
    ["              \"schema\": \"my-schema 1.2\", \"policy\": \"public\"})", ""],
    ["", ""],
    ["result = execute(p, p.request(", ""],
    ["    \"reachability\", sources=[\"test:1\"],", ""],
    ["    scope=[(\"USES\", \"forward\"), (\"REPORTED_IN\", \"forward\")]))", ""],
    ["result.status               ", "# 'completed'"],
    ["result.reachable(\"paper:1\") ", "# True; False only when complete"],
    ["result.path_to(\"paper:1\")   ", "# hops named by original IDs"],
    ["result.to_json()            ", "# normalized request, result_id"],
  ];
  const cw = 6.8, cs = 12;
  card(s, MX, TOP, cw, BOTTOM - TOP, { fill: C.text2, name: "code" });
  const runs = [];
  code.forEach(([c, comment], i) => {
    const last = i === code.length - 1;
    runs.push({ text: c || " ", options: { fontFace: MONO, fontSize: cs, color: C.background1, breakLine: !comment && !last } });
    if (comment) runs.push({ text: comment, options: { fontFace: MONO, fontSize: cs, color: C.accent5, breakLine: !last } });
  });
  const longest = Math.max(...code.map(([c, m]) => (c + m).length));
  check("code width", longest * cs * 0.6 / 72, cw - 0.5);
  check("code height", code.length * cs * 1.18 / 72, BOTTOM - TOP - 0.4);
  s.addText(runs, { isTextBox: true, x: MX + 0.25, y: TOP + 0.2, w: cw - 0.5, h: BOTTOM - TOP - 0.4, margin: 0, valign: "middle", objectName: "quick start" });

  const x0 = MX + cw + 0.4, w = CW - cw - 0.4;
  const rows = [
    ["LuFingerprint", "Original IDs", "Answers and witnesses name your node and edge IDs; parallel edges stay distinct."],
    ["LuHelpCircle", "Three-valued", "`reachable(x)` is True with a witness, False only in a complete answer, None when unknown."],
    ["LuBadgeCheck", "One result contract", "Completed, interrupted, unsupported, failed or stale snapshot, always with the normalized request."],
    ["LuHash", "Reproducible", "The same projection and request give the same `result_id`; only timing is left out of it."],
  ];
  const rh = (BOTTOM - TOP) / rows.length;
  for (let i = 0; i < rows.length; i++) {
    const [ic, head, body] = rows[i];
    const y = TOP + i * rh;
    await iconCircle(s, ic, x0, y + 0.05, 0.5, { label: head });
    check(`code slide ${head}`, 0.3 + height(body, w - 0.7, 14), rh - 0.05);
    text(s, [
      { text: head, options: { fontSize: 16, bold: true, color: C.text1, breakLine: true, paraSpaceAfter: 2 } },
      ...rich(body, { fontSize: 14, color: C.text1 }),
    ], { x: x0 + 0.7, y: y + 0.02, w: w - 0.7, h: rh - 0.05 });
  }
  s.addNotes("The code is README.md's quick start, rewrapped to fit. Sources for the guarantees: CLAUDE.md (Invariants), docs/API_CONTRACT.md §5 (statuses; reachable/affected/found give False only for a completed result; result_id digests everything but timing).");
}

async function toolsSlide() {
  const s = contentSlide("Architecture", "Built for agents: tools, CLI and MCP",
    "The same ten tools from Python, the command line and the Model Context Protocol: JSON in, JSON out.");
  const tools = [
    ["graph_status", "versions, operations, what the session holds"],
    ["graph_describe", "the contract's JSON Schema"],
    ["graph_projection", "build a projection, keep it by identity"],
    ["graph_execute", "run a request; a summary or the whole answer"],
    ["graph_result", "a held result: whole, a slice, one witness"],
    ["graph_batch", "several requests on one projection"],
    ["graph_verify", "check a result against the records"],
    ["graph_revalidate", "which cached results still hold after edits"],
    ["graph_search", "exact text search (needs numpy)"],
    ["graph_conformance", "the conformance suite against this runtime"],
  ];
  const tw = 7.0;
  const rows = tools.map(([n, d], i) => [
    { text: n, options: { fontFace: MONO, fontSize: 13, bold: true, color: C.accent1, fill: { color: i % 2 ? C.background1 : C.background2 } } },
    { text: d, options: { fontSize: 14, color: C.text1, fill: { color: i % 2 ? C.background1 : C.background2 } } },
  ]);
  s.addTable(rows, { x: MX, y: TOP, w: tw, colW: [2.55, tw - 2.55], rowH: 0.47, valign: "middle", margin: [0.03, 0.1, 0.03, 0.15], border: { type: "none" } });

  const x0 = MX + tw + 0.35, w = CW - tw - 0.35;
  let y = TOP;
  const block = async (ic, head, body, fill = C.background2) => {
    const h = 0.68 + height(body, w - 0.4, 14) + 0.14;
    card(s, x0, y, w, h, { fill, name: head });
    await iconCircle(s, ic, x0 + 0.2, y + 0.16, 0.42, { label: head });
    text(s, head, { x: x0 + 0.75, y: y + 0.16, w: w - 0.9, h: 0.42, fontSize: 16, bold: true, color: C.text1, valign: "middle" });
    text(s, rich(body, { fontSize: 14, color: C.text1 }), { x: x0 + 0.2, y: y + 0.68, w: w - 0.4, h: h - 0.74 });
    y += h + 0.22;
  };
  await block("LuListOrdered", "Answers sized for an agent", "Sessions keep projections and results by identity. `view: summary` gives status, counts and nearest items: a fifth the size of a 40-node reachability result.");
  const codeH = 0.78;
  card(s, x0, y, w, codeH, { fill: C.text2, name: "mcp command" });
  s.addText([
    { text: "claude mcp add graph -- \\", options: { breakLine: true } },
    { text: "    python -m graph_runtime mcp" },
  ], { isTextBox: true, x: x0 + 0.2, y, w: w - 0.4, h: codeH, fontFace: MONO, fontSize: 13, color: C.background1, valign: "middle", margin: 0, objectName: "mcp command text" });
  y += codeH + 0.22;
  await block("LuSearch", "Search, then traverse", "Optional exact text search whose hits name the nodes a traversal starts from: SCALE's own hits in all 1,980 parity searches.");
  check("tools right column", y - 0.22, BOTTOM);
  s.addNotes("Sources: README.md (Tools for agents and other programs), docs/RETRIEVAL.md (parity with SCALE: 1,980 searches, 130,173 hits in SCALE's order with SCALE's scores), git log 699d263 (a summary of a 40-node reachability is a fifth the size of the result).");
}

async function overviewSlide() {
  pres.addSection({ title: "Operations" });
  const s = contentSlide("Operations", `${Word(K.meta.operations)} exact operations in ${word(K.overview.length)} families`,
    "Every operation keeps original IDs and canonical order, honours its limits and returns witnesses; most prove themselves on request.");
  const cols = 3, gap = 0.3, w = (CW - (cols - 1) * gap) / cols, h = (BOTTOM - TOP - gap) / 2;
  for (let i = 0; i < K.overview.length; i++) {
    const f = K.overview[i];
    const x = MX + (i % cols) * (w + gap), y = TOP + Math.floor(i / cols) * (h + gap);
    card(s, x, y, w, h, { name: `family ${f.family}` });
    await iconCircle(s, f.icon, x + 0.22, y + 0.22, 0.55, { label: f.family });
    text(s, f.family, { x: x + 0.92, y: y + 0.22, w: w - 1.1, h: 0.55, fontSize: 20, bold: true, color: C.text1, valign: "middle" });
    if (f.isNew) pill(s, "new", x + w - 0.22 - 0.6, y + 0.36, { w: 0.6 });
    const ops = f.ops.join(" · ");
    const opsH = height(ops, w - 0.44, 12, 1.15, { mono: true });
    text(s, [{ text: ops, options: { fontFace: MONO, fontSize: 12, color: C.accent1, bold: true } }], { x: x + 0.22, y: y + 0.92, w: w - 0.44, h: opsH + 0.05 });
    const ansY = y + 0.92 + opsH + 0.15;
    check(`overview ${f.family}`, ansY - y + height(f.answers, w - 0.44, 14), h - 0.15);
    text(s, f.answers, { x: x + 0.22, y: ansY, w: w - 0.44, h: y + h - ansY - 0.15, fontSize: 14, color: C.text1 });
  }
  const fresh = K.families.flatMap((f) => f.ops.filter((o) => o.isNew).map((o) => o.name));
  s.addNotes(`Source: src/graph_runtime/request.py (OPERATIONS: ${K.meta.operations} names), docs/API_CONTRACT.md §2 and §4. Periodic projections are a kind of projection, not an operation: reachability and dependency impact run on them, and periodicity runs only on them.${fresh.length ? ` Implemented and tested but not yet committed: ${fresh.join(", ")}.` : ""}`);
}

// One slide per operation family (content.js, families).
async function familySlide(fam) {
  const s = contentSlide("Operations", fam.slideTitle, fam.lede);
  const footerH = fam.footer ? 0.55 : 0;
  const bottom = BOTTOM - (footerH ? footerH + 0.2 : 0);
  const asideW = fam.aside ? 5.0 : 0, gap = 0.3;
  const n = fam.ops.length;
  const areaW = CW - (asideW ? asideW + gap : 0);
  const colW = (areaW - gap * (n - 1)) / n, h = bottom - TOP;
  // The largest body size (16, 15 or 14 pt) at which every card of the slide fits, the same for all of them.
  const size = [16, 15, 14].find((pt) => fam.ops.every((op) => opMetrics(op, colW, pt).total <= h - 0.15)) || 14;
  for (let i = 0; i < n; i++) await opCard(s, fam.ops[i], MX + i * (colW + gap), TOP, colW, h, fam.icon, size);
  if (fam.aside) ASIDES[fam.aside](s, MX + areaW + gap, TOP, asideW, h);
  if (fam.footer) await FOOTERS[fam.footer](s, MX, bottom + 0.2, CW, footerH);
  s.addNotes([`Sources: ${fam.source}.`, ...fam.ops.map((o) => `${o.name}: ${o.title}. Theory: ${o.theory} Algorithm: ${o.algorithm} ${o.proofLabel}: ${o.proof}`.replace(/`/g, ""))].join("\n\n"));
}

// Heights (inches) of an operation card's parts at a body size.
const OP = { pad: 0.22, d: 0.56 };
function opMetrics(op, w, pt) {
  const tw = w - 2 * OP.pad, hw = tw - OP.d - 0.16 - (op.isNew ? 0.7 : 0);
  const headH = Math.max(OP.d, height(op.name, hw, 15, 1.15, { mono: true }) + height(op.title, hw, 15, 1.15, { bold: true }));
  const bodyH = height(`Theory. ${op.theory}`, tw, pt) + (pt * 0.6) / 72 + height(`Algorithm. ${op.algorithm}`, tw, pt);
  const proofTextH = height(op.proof, tw - 0.12, pt);
  const proofH = 0.44 + proofTextH + 0.12;
  return { tw, hw, headH, bodyH, proofTextH, proofH, total: OP.pad + headH + 0.18 + bodyH + 0.12 + proofH + OP.pad };
}

async function opCard(s, op, x, y, w, h, icon, pt) {
  const { pad, d } = OP;
  const M = opMetrics(op, w, pt), tw = M.tw;
  card(s, x, y, w, h, { name: `operation ${op.name}` });
  await iconCircle(s, icon, x + pad, y + pad, d, { label: op.name });

  // Header: the operation's name (monospace) and what it answers, beside the icon.
  const hx = x + pad + d + 0.16;
  text(s, [
    { text: op.name, options: { fontFace: MONO, fontSize: 15, bold: true, color: C.accent1, breakLine: true } },
    { text: op.title, options: { fontSize: 15, bold: true, color: C.text1 } },
  ], { x: hx, y: y + pad - 0.02, w: M.hw, h: M.headH + 0.04, valign: "middle" });
  if (op.isNew) pill(s, "new", x + w - pad - 0.6, y + pad + 0.02, { w: 0.6 });

  const py = y + h - pad - M.proofH; // the proof box sits at the bottom of the card

  // Theory and algorithm, each led by its name, between the header and the proof box.
  const by = y + pad + M.headH + 0.18;
  const para = pt * 0.6;
  const theory = [{ text: "Theory. ", options: { fontSize: pt, bold: true, color: C.accent1, paraSpaceAfter: para } }, ...rich(op.theory, { fontSize: pt, color: C.text1, paraSpaceAfter: para })];
  theory[theory.length - 1].options.breakLine = true;
  const algorithm = [{ text: "Algorithm. ", options: { fontSize: pt, bold: true, color: C.accent1 } }, ...rich(op.algorithm, { fontSize: pt, color: C.text1 })];
  check(`op ${op.name} body`, M.bodyH, py - by - 0.08);
  text(s, [...theory, ...algorithm], { x: x + pad, y: by, w: tw, h: Math.max(0.5, py - by - 0.08) });

  // The proof box, sized to its text (added after the body, so it is read after it).
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x + pad - 0.06, y: py, w: tw + 0.12, h: M.proofH, rectRadius: 0.08, fill: { color: C.background1 }, line: { color: C.accent1, width: 1.25 }, objectName: `proof ${op.name}` });
  s.addImage({ data: await iconData("LuShieldCheck", HEX.accent1), x: x + pad + 0.06, y: py + 0.1, w: 0.27, h: 0.27, altText: "certificate" });
  text(s, [{ text: op.proofLabel.toUpperCase(), options: { fontSize: 11, bold: true, color: C.accent1, charSpacing: 1 } }], { x: x + pad + 0.42, y: py + 0.1, w: tw - 0.5, h: 0.27, valign: "middle" });
  text(s, rich(op.proof, { fontSize: pt, color: C.text1 }), { x: x + pad + 0.06, y: py + 0.42, w: tw - 0.12, h: M.proofTextH + 0.06 });
}

const ASIDES = {
  // Schematic intervals: the first three are certified as the top set (their lowest lower bound exceeds every other
  // upper bound); the order inside and below them is not.
  rankingSchematic(s, x, y, w, h) {
    card(s, x, y, w, h, { fill: C.background1, line: { color: C.background2, width: 1.5 }, name: "ranking schematic" });
    text(s, [
      { text: "SCHEMATIC", options: { fontSize: 11, bold: true, color: C.accent6, charSpacing: 1, breakLine: true } },
      { text: "Each node's PageRank lies in its interval", options: { fontSize: 16, bold: true, color: C.text1 } },
    ], { x: x + 0.3, y: y + 0.25, w: w - 0.6, h: 0.65 });
    const iv = [[0.80, 0.97], [0.64, 0.82], [0.56, 0.68], [0.32, 0.52], [0.26, 0.48], [0.08, 0.40]];
    const ax = x + 1.0, aw = w - 1.4, top = y + 1.15, rh = 0.4;
    const cut = iv[2][0];
    iv.forEach(([lo, hi], i) => {
      const yy = top + i * rh;
      text(s, [{ text: `v${i + 1}`, options: { fontFace: MONO, fontSize: 13, color: C.text1 } }], { x: x + 0.3, y: yy, w: 0.6, h: 0.3, valign: "middle" });
      const certified = i < 3;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: ax + lo * aw, y: yy + 0.06, w: (hi - lo) * aw, h: 0.18, rectRadius: 0.09,
        fill: { color: certified ? C.accent1 : C.accent6 }, line: { type: "none" }, objectName: `interval v${i + 1}`,
      });
    });
    const lx = ax + cut * aw;
    line(s, lx, top - 0.1, lx, top + iv.length * rh, { color: C.accent2, width: 2, dashType: "dash" });
    text(s, [{ text: "certified_top = 3", options: { fontFace: MONO, fontSize: 13, bold: true, color: C.accent2 } }], { x: lx - 2.05, y: top + iv.length * rh + 0.05, w: 2.0, h: 0.3, align: "right" });
    const ny = top + iv.length * rh + 0.45;
    text(s, [
      { text: "The lowest lower bound of v1–v3 exceeds every other upper bound, so they are proven to be the top three as a set. v4 and v5 overlap: their order is not claimed.", options: { fontSize: 14, color: C.text1 } },
    ], { x: x + 0.3, y: ny, w: w - 0.6, h: y + h - ny - 0.2 });
    check("ranking schematic note", ny - y + height("The lowest lower bound of v1–v3 exceeds every other upper bound, so they are proven to be the top three as a set. v4 and v5 overlap: their order is not claimed.", w - 0.6, 14), h - 0.2);
  },
};

const FOOTERS = {
  async ranks(s, x, y, w, h) {
    const chips = [["0", "molecule"], ["1", "chain"], ["2", "layer"], ["3", "framework"]];
    const lw = 0.75, copiesW = 3.2, gap = 0.2, cw = (w - lw - copiesW - gap * 4) / 4;
    text(s, [{ text: "RANK", options: { fontSize: 11, bold: true, color: C.accent6, charSpacing: 1 } }], { x, y, w: lw, h, valign: "middle" });
    chips.forEach(([r, name], i) => {
      const cx = x + lw + i * (cw + gap);
      card(s, cx, y, cw, h, { name: `rank ${r}` });
      text(s, [
        { text: `${r}  `, options: { fontSize: 22, bold: true, color: C.accent1, fontFace: "Cambria" } },
        { text: name, options: { fontSize: 15, color: C.text1 } },
      ], { x: cx + 0.2, y, w: cw - 0.3, h, valign: "middle" });
    });
    const lx = x + lw + 4 * (cw + gap);
    text(s, rich("`copies`: the degree of interpenetration", { fontSize: 14, color: C.accent6 }), { x: lx, y, w: x + w - lx, h, valign: "middle" });
  },
};

async function revalidationSlide() {
  pres.addSection({ title: "Trust and results" });
  const R = K.revalidation;
  const s = contentSlide("Trust and results", "Revalidation: is a cached answer still right?",
    "Decided from the answer and its certificate, in time proportional to the edit, without searching again.");
  const verdicts = [
    ["LuCheckCircle2", C.accent1, "holds", "Still a correct answer to the same request on the new projection."],
    ["LuXCircle", C.accent4, "fails", "Refuted, with the edit that refutes it: a witness edge gone, or a hop to a node the answer does not list."],
    ["LuHelpCircle", C.accent2, "unknown", "Not decidable without a new search: interrupted, uncertified, or an edit broke the proof without refuting it."],
  ];
  const lw = 5.55, vh = (BOTTOM - TOP - 2 * 0.25) / 3;
  for (let i = 0; i < verdicts.length; i++) {
    const [ic, fill, word, body] = verdicts[i];
    const y = TOP + i * (vh + 0.25);
    card(s, MX, y, lw, vh, { name: `verdict ${word}` });
    await iconCircle(s, ic, MX + 0.25, y + (vh - 0.62) / 2, 0.62, { fill, label: word });
    check(`verdict ${word}`, 0.35 + height(body, lw - 1.35, 14), vh - 0.2);
    text(s, [
      { text: word, options: { fontFace: MONO, fontSize: 18, bold: true, color: fill, breakLine: true, paraSpaceAfter: 2 } },
      { text: body, options: { fontSize: 14, color: C.text1 } },
    ], { x: MX + 1.1, y: y + 0.12, w: lw - 1.3, h: vh - 0.2, valign: "middle" });
  }
  const x0 = MX + lw + 0.45, w = CW - lw - 0.45;
  // The two numbers.
  const half = (w - 0.3) / 2;
  [[R.fast, "per cached result, checked against a diff", C.accent1], [R.slow, "to rerun the same request", C.accent6]].forEach(([big, label, color], i) => {
    const x = x0 + i * (half + 0.3);
    text(s, [
      { text: big, options: { fontSize: 44, bold: true, color, fontFace: "Cambria", breakLine: true } },
      { text: label, options: { fontSize: 14, color: C.text1 } },
    ], { x, y: TOP, w: half, h: 1.3 });
  });
  text(s, R.context, { x: x0, y: TOP + 1.32, w, h: 0.3, fontSize: 12, color: C.accent6, italic: true });
  const chY = TOP + 1.85;
  s.addChart(pres.charts.BAR, [{ name: "SCALE verdicts", labels: R.scale.labels, values: R.scale.values }], {
    x: x0, y: chY, w, h: BOTTOM - chY - 0.35, barDir: "bar", catAxisOrientation: "maxMin", valAxisHidden: true,
    chartColors: [HEX.accent1, HEX.accent4, HEX.accent2], barGapWidthPct: 45,
    showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "#,##0",
    ...chartStyle({ title: "SCALE: 345 cached answers across 31 curation edits", catAxisLabelFontSize: 13 }),
    objectName: "SCALE verdicts chart",
  });
  text(s, "Each verdict confirmed by a fresh run.", { x: x0, y: BOTTOM - 0.3, w, h: 0.3, fontSize: 12, color: C.accent6, italic: true });
  s.addNotes(`Sources: ${R.source}; docs/API_CONTRACT.md §7 (verdicts and reasons). With one diff of the projections, each result is checked in time proportional to the edit (binary search in the answer's canonical lists). On 700 random edits the diff and full checks give the same record and every 'holds' matches a fresh run. Measured on a heavily loaded machine.`);
}

async function verificationSlide() {
  const s = contentSlide("Trust and results", "How we know the answers are right",
    "Four independent checks, none of which trusts the engine's own output.");
  const n = K.verification.length, gap = 0.25, tw = (CW - gap * (n - 1)) / n, th = 1.3;
  K.verification.forEach((v, i) => {
    const x = MX + i * (tw + gap);
    card(s, x, TOP, tw, th, { fill: C.text2, name: `count ${v.label}` });
    text(s, [
      { text: v.big, options: { fontSize: 34, bold: true, color: C.background1, fontFace: "Cambria", breakLine: true } },
      { text: v.label, options: { fontSize: 13, color: C.accent5 } },
    ], { x: x + 0.2, y: TOP + 0.12, w: tw - 0.4, h: th - 0.2 });
  });
  const methods = [
    ["LuShieldCheck", "Independent verifier", "`verify_result` re-checks every witness, closure and certificate against the input records, not the engine's state."],
    ["LuFlaskConical", "Independent oracles", "Separate implementations on random graphs: Brzozowski derivatives, exact PageRank over fractions, brute-force enumerators, NetworkX."],
    ["LuListChecks", "Hand-worked cases", "Language-neutral cases whose expectations are worked out by hand, never copied from the engine; any backend must pass them."],
    ["LuGitCompare", "Consumer parity", "SCALE: 18,951 records on injected retractions, 1,654 generated chains. STATES: 760 contexts, 38,033 atom images."],
  ];
  const my = TOP + th + 0.3, mh = BOTTOM - my - 0.45, mw = (CW - 3 * 0.3) / 4;
  for (let i = 0; i < methods.length; i++) {
    const [ic, head, body] = methods[i];
    const x = MX + i * (mw + 0.3);
    card(s, x, my, mw, mh, { name: head });
    await iconCircle(s, ic, x + 0.22, my + 0.22, 0.5, { label: head });
    text(s, head, { x: x + 0.22, y: my + 0.85, w: mw - 0.44, h: 0.32, fontSize: 16, bold: true, color: C.text1 });
    check(`method ${head}`, 1.25 + height(body, mw - 0.44, 14), mh - 0.1);
    text(s, rich(body, { fontSize: 14, color: C.text1 }), { x: x + 0.22, y: my + 1.25, w: mw - 0.44, h: mh - 1.35 });
  }
  text(s, "Also: an independent review that fuzzed the runtime (about 50,000 cases), and a Codex review of the contract (v0.1). Golden test: 1,200 pinned result_ids.", { x: MX, y: BOTTOM - 0.32, w: CW, h: 0.32, fontSize: 12, color: C.accent6, italic: true });
  s.addNotes("Counts as of this build: python3 -m pytest (499 pass), src/graph_runtime/conformance/cases.json (133 cases), SCALE parity (16 tests) and STATES parity (6 tests) in their environments; 73 of 73 real consumer requests answer the same with both engines (git log f144c48). Oracles: tests/oracle.py; regular paths against Brzozowski derivatives on 1,500 random graphs (git log f65497b); PageRank against the exact linear system over fractions on 500 graphs (git log 0d4d2b3). Parity: docs/SCALE_INTEGRATION.md, docs/STATES_INTEGRATION.md. Reviews: git log 8917563, 7ff04db.");
}

async function speedSlide() {
  const s = contentSlide("Trust and results", "Measured speed on large graphs",
    "Pure Python, standard library only, exact throughout.");
  const cols = 3, gap = 0.3, w = (CW - gap * (cols - 1)) / cols, h = 2.2;
  K.speedTiles.forEach((t, i) => {
    const x = MX + (i % cols) * (w + gap), y = TOP + Math.floor(i / cols) * (h + gap);
    card(s, x, y, w, h, { name: `tile ${t.label}` });
    check(`tile ${t.label}`, 1.05 + 0.4 + height(t.context, w - 0.5, 14), h - 0.15);
    text(s, [
      { text: t.big, options: { fontSize: 44, bold: true, color: C.accent1, fontFace: "Cambria", breakLine: true } },
      { text: t.label, options: { fontSize: 17, bold: true, color: C.text1, breakLine: true, paraSpaceAfter: 6 } },
      { text: t.context, options: { fontSize: 14, color: C.accent6 } },
    ], { x: x + 0.28, y: y + 0.18, w: w - 0.56, h: h - 0.3 });
  });
  text(s, "Measured on a heavily loaded machine: ratios are more reliable than absolute times.", { x: MX, y: TOP + 2 * h + gap + 0.08, w: CW, h: 0.3, fontSize: 12, color: C.accent6, italic: true });
  s.addNotes(K.speedTiles.map((t) => `${t.big} ${t.label} (${t.context}). Source: ${t.source}.`).join("\n"));
}

async function headToHeadSlide() {
  const s = contentSlide("Trust and results", "Head to head: STATES's code and networkx",
    "The same answers as the applications' own code; GRAPH's carry witnesses and certificates.");
  const w = (CW - 0.4) / 2, ch = BOTTOM - TOP - 0.75;
  const S1 = K.statesChart;
  s.addChart(pres.charts.BAR, [{ name: "ms per context", labels: S1.labels, values: S1.values }], {
    x: MX, y: TOP, w, h: ch, barDir: "bar", catAxisOrientation: "maxMin", valAxisHidden: true,
    chartColors: [HEX.accent6, HEX.accent5, HEX.accent1], barGapWidthPct: 45,
    showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "General",
    ...chartStyle({ title: "STATES contexts: mean ms per context (42 contexts)" }),
    objectName: "STATES chart",
  });
  text(s, "Identical answers on all 760 parity contexts. The largest context: 3.9 s through a lifted box, 29 ms on the periodic cell.", { x: MX, y: TOP + ch + 0.1, w, h: 0.6, fontSize: 13, color: C.text1 });
  const S2 = K.structureChart;
  const x2 = MX + w + 0.4;
  s.addChart(pres.charts.BAR, [{ name: "seconds", labels: S2.labels, values: S2.values }], {
    x: x2, y: TOP, w, h: ch, barDir: "bar", catAxisOrientation: "maxMin", valAxisHidden: true,
    chartColors: S2.graph.map((g) => (g ? HEX.accent1 : HEX.accent6)), barGapWidthPct: 45,
    showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0.00",
    ...chartStyle({ title: "100k nodes, 500k edges (about 250k in scope): seconds" }),
    objectName: "structure chart",
  });
  text(s, "networkx is pure Python too. GRAPH's components and biconnectivity answers come with certificates.", { x: x2, y: TOP + ch + 0.1, w, h: 0.6, fontSize: 13, color: C.text1 });
  s.addNotes(`Left: ${S1.source}. Mean of 42 contexts: STATES's extract_context 58.5 ms; GRAPH through a lift 305 ms (222 ms lift + 0.34 ms query); GRAPH on the cell 17.1 ms (10.6 ms projection + 0.76 ms query). The cell's projection depends only on the record, so it can be built once and reused. Right: ${S2.source}. Teal: GRAPH; gray: networkx. Heavily loaded machine: compare ratios.`);
}

async function timelineSlide() {
  pres.addSection({ title: "Development" });
  const s = contentSlide("Development", "Development timeline", K.timelineLede);
  const lx = MX + 1.05, x0 = MX, tw = 6.6;
  const rows = K.timeline;
  const pitch = (BOTTOM - TOP) / rows.length;
  line(s, lx, TOP + pitch / 2, lx, TOP + (rows.length - 0.5) * pitch, { color: C.accent5, width: 2 });
  rows.forEach((r, i) => {
    const y = TOP + i * pitch;
    if (r.day) {
      text(s, [{ text: r.day.toUpperCase(), options: { fontSize: 12, bold: true, color: C.accent1, charSpacing: 1 } }], { x: lx + 0.3, y, w: tw - 1.4, h: pitch, valign: "middle" });
      return;
    }
    const d = r.isNew ? 0.2 : 0.16;
    s.addShape(pres.shapes.OVAL, { x: lx - d / 2, y: y + pitch / 2 - d / 2, w: d, h: d, fill: { color: r.isNew ? C.accent2 : C.accent1 }, line: { color: C.background1, width: 1.5 }, objectName: `milestone ${r.when}` });
    text(s, [{ text: r.when, options: { fontSize: 13, color: C.accent6 } }], { x: x0, y, w: lx - x0 - 0.2, h: pitch, align: "right", valign: "middle" });
    check(`timeline ${r.when}`, height(r.what, tw - 1.35, 14) , pitch + 0.02);
    text(s, rich(r.what, { fontSize: 14, color: C.text1 }), { x: lx + 0.3, y, w: tw - 1.35, h: pitch, valign: "middle" });
  });
  const x2 = MX + tw + 0.35, w2 = CW - tw - 0.35;
  const G = K.growth;
  [[G.operations, "operations"], [G.tests, "runtime tests"]].forEach(([big, label], i) => {
    const w = (w2 - 0.25) / 2, x = x2 + i * (w + 0.25);
    card(s, x, TOP, w, 1.1, { fill: C.text2, name: `growth ${label}` });
    text(s, [
      { text: big, options: { fontSize: 28, bold: true, color: C.background1, fontFace: "Cambria", breakLine: true } },
      { text: label, options: { fontSize: 13, color: C.accent5 } },
    ], { x: x + 0.2, y: TOP + 0.1, w: w - 0.4, h: 0.95 });
  });
  s.addChart(pres.charts.LINE, [{ name: "conformance cases", labels: G.labels, values: G.cases }], {
    x: x2, y: TOP + 1.35, w: w2, h: BOTTOM - TOP - 1.35, chartColors: [HEX.accent1], lineSize: 2.5,
    lineDataSymbol: "circle", lineDataSymbolSize: 7, showValue: true, dataLabelPosition: "t",
    valAxisHidden: true, catAxisLabelRotate: 315,
    ...chartStyle({ title: "Hand-worked conformance cases", catAxisLabelFontSize: 11, dataLabelFontSize: 11 }),
    objectName: "conformance growth chart",
  });
  s.addNotes("Source: git log (20 commits, 550c362 to 7b39f7d) and the working tree (components and biconnectivity: src/graph_runtime/structure.py, tests/test_structure.py, not yet committed). Conformance case counts are from the commit messages (34 at v0, then 49, 54, 62, 70, 74, 78, 91, 100, 112, 116, 121) and cases.json now (133). Tests: 178 at v0 (550c362), 499 now. Commit times are local (-0400).");
}

async function roadmapSlide() {
  pres.addSection({ title: "Roadmap" });
  const s = contentSlide("Roadmap", "Roadmap: the next increments",
    "From two literature surveys run with Codex. Structural decomposition, which both recommend, is done; these come next.");
  const cols = [
    { key: "name", label: "INCREMENT", w: 2.95 },
    { key: "answers", label: "WHAT IT ANSWERS", w: 3.55 },
    { key: "cert", label: "CERTIFICATE", w: 3.55 },
    { key: "ref", label: "REFERENCES", w: CW - 2.95 - 3.55 - 3.55 },
  ];
  const hy = TOP - 0.08;
  let cx = MX;
  for (const c of cols) {
    text(s, [{ text: c.label, options: { fontSize: 11, bold: true, color: C.accent6, charSpacing: 1 } }], { x: cx + (c.key === "name" ? 0.85 : 0.15), y: hy, w: c.w - 0.2, h: 0.25 });
    cx += c.w;
  }
  const ry = hy + 0.32, n = K.roadmap.length, gap = 0.1, rh = (BOTTOM - ry - gap * (n - 1)) / n;
  for (let i = 0; i < n; i++) {
    const r = K.roadmap[i];
    const y = ry + i * (rh + gap);
    card(s, MX, y, CW, rh, { name: `roadmap ${r.name}` });
    await iconCircle(s, r.icon, MX + 0.18, y + (rh - 0.48) / 2, 0.48, { label: r.name });
    let x = MX;
    for (const c of cols) {
      const isName = c.key === "name";
      const tx = x + (isName ? 0.85 : 0.15), tw = c.w - (isName ? 0.95 : 0.25);
      const size = c.key === "ref" ? 12 : isName ? 15 : 14;
      check(`roadmap ${r.name} ${c.key}`, height(r[c.key], tw, size), rh - 0.08);
      text(s, [{ text: r[c.key], options: { fontSize: size, bold: isName, color: c.key === "ref" ? C.accent6 : C.text1, italic: c.key === "ref" } }], { x: tx, y, w: tw, h: rh, valign: "middle" });
      x += c.w;
    }
  }
  s.addNotes("Sources: plan.md and the two Codex surveys (codex_literature.md, codex_domains.md), 26 October 2026, kept outside the repository. Each increment is planned to ship with code, docs/API_CONTRACT.md, the schema, hand-worked conformance cases, tests with a networkx oracle, verification and revalidation, golden result_ids unchanged, and a Codex adversarial review of its diff. Further candidates: DAG critical path, d-separation, minimum spanning forests.");
}

async function boundariesSlide() {
  const s = contentSlide("Roadmap", "Boundaries and open questions",
    "What the surveys advise GRAPH to leave to others, and what the group should decide next.");
  const B = K.boundaries;
  const w = (CW - 0.4) / 2, h = BOTTOM - TOP - 0.55;
  const col = async (x, ic, fill, head, items) => {
    card(s, x, TOP, w, h, { name: head });
    await iconCircle(s, ic, x + 0.25, TOP + 0.25, 0.55, { fill, label: head });
    text(s, head, { x: x + 0.95, y: TOP + 0.25, w: w - 1.2, h: 0.55, fontSize: 20, bold: true, color: C.text1, valign: "middle" });
    const runs = items.map((t, i) => ({ text: t, options: { fontSize: 15, color: C.text1, bullet: { indent: 18 }, paraSpaceAfter: 14, breakLine: i < items.length - 1 } }));
    const need = items.reduce((a, t) => a + height(t, w - 0.85, 15) + 0.2, 0);
    check(`boundaries ${head}`, need, h - 1.1);
    text(s, runs, { x: x + 0.3, y: TOP + 1.05, w: w - 0.6, h: h - 1.2 });
  };
  await col(MX, "LuBan", C.accent4, "Out of scope (survey advice)", B.wont);
  await col(MX + w + 0.4, "LuHelpCircle", C.accent2, "Open questions", B.open);
  text(s, B.alsoSurveyed, { x: MX, y: BOTTOM - 0.4, w: CW, h: 0.4, fontSize: 12, color: C.accent6, italic: true, valign: "middle" });
  s.addNotes("Sources: codex_domains.md (section 3, things GRAPH should deliberately not do; section 4, open questions) and codex_literature.md (considered and rejected or deferred; open questions). These are the surveys' recommendations, not decisions.");
}

async function closingSlide() {
  pres.addSection({ title: "Close" });
  const s = pres.addSlide({ masterName: "GRAPH_DARK", sectionTitle: "Close" });
  s.addText("One exact, checkable graph layer for every project", { placeholder: "title" });
  const items = [
    `${Word(K.meta.operations)} operations with witnesses; certificates on request; revalidation after edits.`,
    "SCALE and STATES integrated, with parity tests; RATES and Cathanor to follow.",
    "Start with docs/API_CONTRACT.md, or ask the tools:",
  ];
  s.addText(items.map((t, i) => ({ text: t, options: { bullet: { indent: 20 }, color: C.background1, fontSize: 18, paraSpaceAfter: 12, breakLine: i < items.length - 1 } })), { placeholder: "body" });
  card(s, 0.8, 5.0, 7.4, 1.0, { fill: C.text1, name: "commands" });
  s.addText([
    { text: "python -m graph_runtime call graph_status", options: { breakLine: true } },
    { text: "claude mcp add graph -- python -m graph_runtime mcp" },
  ], { isTextBox: true, x: 1.05, y: 5.0, w: 7.0, h: 1.0, fontFace: MONO, fontSize: 13, color: C.accent5, valign: "middle", margin: 0, objectName: "commands text" });
  check("closing command width", "claude mcp add graph -- python -m graph_runtime mcp".length * 13 * 0.6 / 72, 7.0);
  text(s, [{ text: `hlxin/graph (private) · ${K.meta.author}`, options: { fontSize: 12, color: C.accent5 } }], { x: 0.8, y: 6.55, w: 7.4, h: 0.3 });
  graphMotif(s, 9.0, 1.1, 3.6, 4.9, { scale: 0.9 });
  s.addNotes("Commands from README.md and CLAUDE.md (run from the repository with PYTHONPATH=src, or after installing the package). 'RATES and Cathanor to follow' reflects README.md's list of applications; neither has an adapter yet.");
}

const SLIDES = [
  titleSlide,
  motivationSlide,
  principlesSlide,
  architectureSlide,
  codeSlide,
  toolsSlide,
  overviewSlide,
  ...K.families.map((f) => () => familySlide(f)),
  revalidationSlide,
  verificationSlide,
  speedSlide,
  headToHeadSlide,
  timelineSlide,
  roadmapSlide,
  boundariesSlide,
  closingSlide,
];

// ------------------------------------------------------------------------------------------------- theme part
// pptxgenjs writes Office's palette into ppt/theme/theme1.xml; write THEME's colors and name there instead, so
// scheme colors resolve to the deck's palette in PowerPoint.
async function writeTheme(file) {
  const JSZip = require(require.resolve("jszip", { paths: [path.dirname(require.resolve("pptxgenjs"))] }));
  const zip = await JSZip.loadAsync(fs.readFileSync(file));
  const part = "ppt/theme/theme1.xml";
  const xml = await zip.file(part).async("string");
  const slots = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"];
  const scheme = `<a:clrScheme name="${THEME.name}">${slots.map((k) => `<a:${k}><a:srgbClr val="${THEME.colors[k]}"/></a:${k}>`).join("")}</a:clrScheme>`;
  const next = xml.replace(/<a:clrScheme\b[\s\S]*?<\/a:clrScheme>/, scheme).replace(/(<a:theme\b[^>]*?\bname=")[^"]*"/, `$1${THEME.name}"`);
  if (!next.includes(scheme)) throw new Error("no color scheme found in ppt/theme/theme1.xml");
  zip.file(part, next);
  for (const name of Object.keys(zip.files)) {
    if (!name.endsWith(".xml")) continue;
    const bad = (await zip.file(name).async("string")).match(/<a:srgbClr val="((?![0-9A-Fa-f]{6}")[^"]*)"/);
    if (bad) throw new Error(`${name}: color "${bad[1]}" is not six hex digits (a scheme color in a hex-only option)`);
  }
  fs.writeFileSync(file, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }));
}

async function main() {
  pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  pres.title = "GRAPH: Graph Runtime for Analysis, Provenance, Hypotheses";
  pres.subject = "Key theory, development and results of the GRAPH library";
  pres.author = "Hongliang Xin";
  pres.company = "Virginia Tech";
  C = pres.SchemeColor;
  defineLayouts();
  for (const make of SLIDES) await make();
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  await pres.writeFile({ fileName: OUT });
  await writeTheme(OUT);
  console.log(`wrote ${OUT} (${SLIDES.length} slides)`);
  if (warnings.length) {
    console.log(`\n${warnings.length} possible overflow(s); shorten the text in content.js or check the render:`);
    for (const w of warnings) console.log(`  - ${w}`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
