# JAMT reference typesetter

The 2026.5 master template combines 24 published PDFs (293 pages, 2024 issues 1–4),
five earlier PDF references and two genuinely editable final Word articles.
The 24 Word copies in the new corpus contain page images, not editable articles;
they are visual references only. The original corpus is not committed to Git.
`jamt.cls` is the reusable XeLaTeX class; article text, current author
metadata, figures, tables and equations are filled by the native DOCX bridge.
No model weights are trained. Qwen provides constrained advice and visual review.

## Master template

`master_template.py` generates `jamt-profile.tex` from the same versioned JSON
used by the native Word editor, including page geometry, columns and all
paragraph roles. `jamt.cls` exposes semantic paragraph, heading, object and
column commands. `master.tex` is the standalone starter; `TEMPLATE.md` explains
each slot. `jamt-reference.cls` remains a compatibility alias.

The JAMT workspace offers an authenticated starter download. Every successful
article export bundles the identical class/profile, their hashes and its own
content/assets. The template itself contains yellow prompts and explicitly
demonstrative material, never borrowed author or publication metadata.

Layout advice is checked against profile ranges: tables stay at 10 pt, body
leading stays in the measured 12.4–12.7 pt range. The planner sees the current
profile rather than a separate hard-coded list of typographic defaults.

## Web workflow

`JAMT_LATEX_EXPORT_ENABLED=1` builds a LaTeX PDF and a buildable TeX archive
after native Word formatting. It defaults to off for installations without TeX.
The input to LaTeX is the final, reviewed DOCX, including its yellow editorial
marks. PDF is never used to recover manuscript text.
The Word companion stays native; it is not a LaTeX-to-DOCX conversion.

The normal editor recognizes existing JAMT structure, geometry and typography.
When at least 85% of eligible paragraphs match the measured font and size, it
preserves the existing sections, tables, drawing anchors and line breaks and
repairs only supported, dominant font/size outliers. An unchanged input is copied
byte-for-byte. The gate uses document structure, not filenames or known hashes.
This does not certify visual quality: rendered review still runs.

A LaTeX candidate that passes the content/compile gate becomes the primary PDF,
unless the visual review flags severe overlap, clipping, illegibility or table
layout problems. Its verified hash is checked again before selection. The PDF
rendered from Word remains downloadable for comparison. A failed gate removes
the candidate PDF/archive and records a reason in `latex-export-report.json`;
the native Word/PDF remain available. Successful output has source/PDF hashes,
formula and list inventories and optional page-by-page Qwen comparison. AI failure
does not discard a mechanically valid PDF. Downloads are owner-only and no-store.
The UI shows the actual PDF engine, checked pages and remaining observations.

Before formatting, `v2/editorial.py` adds yellow prompts for absent RU/EN front
matter, article DOI, UDC, year/volume/issue and received/accepted/published dates.
Exact suspicious text spans are highlighted. Original characters, numbers and
formula nodes are never corrected by this review. Qwen can return only exact,
unique quotes from identified paragraphs, not replacement prose. Its 120-second
budget and checked/unchecked paragraph IDs are reported; deterministic checks
still work when the model is unavailable. This is not a guarantee of finding all
language or scientific errors. No model weights are trained.

Qwen comparison has a 600-second budget and 24-page limit in web jobs. Partial
coverage is explicitly reported, including failed page requests. Worker deadlines
include two bounded XeLaTeX passes, this review and editorial checking. Native Word
repair remains the existing bounded edit/render/check cycle, using only validated
actions and accepting changes only after a non-regressing recheck.

## Standalone comparison

```sh
DJANGO_SETTINGS_MODULE=config.settings python -m paper_formatter.latex_lab source.docx \
  --reference --baseline-docx source.docx --baseline-pdf reference-word.pdf \
  --output /private/experiment --qwen
```

`--reference` reads the source directly. The supplied baseline PDF must show the
same article; it is visual evidence, never a text source. Without `--reference`,
the older experimental path reads the native editor result. `--reuse` retains an
existing baseline. CLI inputs are DOCX; web DOC/DOTX preparation is separate.

`--search` compiles bounded alternatives for review. Density, fewer pages or less
whitespace cannot automatically replace the measured reference layout. A Qwen
proposal is kept separately and requires visual comparison before promotion.

## Content and layout contract

1. Read original visible paragraphs, table cells, drawings, text boxes and math.
   Resolve alternate OOXML branches once. Preserve native image bytes, recording
   crop, mirror and rotation derivatives separately.
2. Match every emitted text node against the source inventory. Missing or duplicated
   nodes fail export. Resolve supported automatic Word list labels explicitly;
   these numbers are not ordinary text nodes in the DOCX.
3. Convert supported OMML math and a strict subset of Equation Native MTEF v3.
   The MTEF parser reads data from OLE without activating embedded objects. Unknown
   records, templates and symbols fail explicitly; Qwen never guesses formulas.
   Single-page Acrobat PDF OLE figures are read as data and copied as vector
   pages without active links, annotations or widgets. Embedded programs never run.
4. Fill the class with full-width bilingual front matter, two-column body, wide
   objects, attached captions, references, author biographies and license. Keep
   source section intent for structurally conforming reference documents. Numeric
   data and adjoining figure panels in one Word layout table are separated
   semantically while retaining all their original nodes.
5. Use current-article headers, author footer and page start when known. No article
   receives another reference's DOI, publication dates or authors. Use Times New
   Roman when installed, otherwise Liberation Serif; math is TeX Gyre Termes.
6. Compile twice with XeLaTeX. Check missing glyphs, overflow, page bounds,
   every source word/number and explicit numbering anchors. These gates do not prove
   mathematical equivalence or publication quality.
7. Compare candidate pages with content-aligned reference pages as blind A/B pairs,
   alternating their order. Record exact coverage, raw model remarks and hashes.
   Model preferences do not rewrite the manuscript. Severe candidate layout
   findings prevent its selection as the primary PDF.

The artifact contract and measurements are documented in
[`docs/JAMT_CORPUS_20260921.md`](../../docs/JAMT_CORPUS_20260921.md).

## Build and limits

Archives contain `main.tex`, `jamt-reference.cls`, assets, font/license, manifest
and layout plan. Compile twice with `xelatex -no-shell-escape main.tex`; TeX Live
and Liberation Serif/DejaVu Sans are required. The application runs the compiler
as its unprivileged account, with restricted file IO and a 180-second timeout per
pass. Uploaded TeX and model-authored commands are never executed.

Supported lists include decimal, zero-padded decimal, Roman, alphabetic and
common bullets, levels 0–8, style inheritance, restarts and start overrides.
Long tables use repeated headers and page breaking outside `multicols` and
minipages. Four-or-more-column data tables are full width by default. Parallel
biography cells are prose, not ruled data tables. Unsupported lists, MTEF versions/templates, nested tables,
footnotes/endnotes, tracked deletions, symbolic-font characters and unknown OMML
structures explicitly stop this optional branch. Sparse cells are padded from
declared grid offsets. Table merges preserve text, but not every Word border or
vertical alignment is reproduced. Font metrics and page/column breaks differ
between Word, LibreOffice and XeLaTeX. Do not promise pixel-identical DOCX/PDF
pagination, universal article support or a quality percentage from two examples.

## Tests

```sh
python manage.py test apps.template_workspace paper_formatter_tests.test_latex_lab \
  paper_formatter_tests.test_mtef --noinput
python tools/analyze_jamt_corpus.py /private/JAMT_corpus_24 /private/evidence --render
python tools/jamt_stress_fixtures.py /private/raw-drafts
python tools/jamt_benchmark.py /private/raw-drafts /private/benchmark --compile
```

Real article rendering and inspection are additionally required. The original
dated lab report documents the earlier experiment, not the current export rules.
