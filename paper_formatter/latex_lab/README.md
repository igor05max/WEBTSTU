# JAMT reference typesetter

The 2026.2 style is measured from five published PDFs and two final Word articles.
`jamt-reference.cls` is the reusable XeLaTeX class; article text, current author
metadata, figures, tables and equations are filled by the native DOCX bridge.
No model weights are trained. Qwen provides constrained advice and visual review.

## Web workflow

`JAMT_LATEX_EXPORT_ENABLED=1` adds an experimental PDF and a buildable TeX archive
to JAMT jobs after the normal DOCX/PDF export. It defaults to off. The input to
LaTeX is the original working DOCX, not a PDF and not a rewritten Word result.
The Word companion stays native; it is not a LaTeX-to-DOCX conversion.

The normal editor recognizes existing JAMT structure, geometry and typography.
When at least 85% of eligible paragraphs match the measured font and size, it
preserves the existing sections, tables, drawing anchors and line breaks and
repairs only supported, dominant font/size outliers. An unchanged input is copied
byte-for-byte. The gate uses document structure, not filenames or known hashes.
This does not certify visual quality: rendered review still runs.

The extra export cannot replace the native results. A failed content/compile
gate removes its downloadable PDF/archive and records a reason in
`latex-export-report.json`. Successful output has its source/PDF hashes, metrics,
formula and numbering inventory, and optional page-by-page Qwen comparison.
AI failure does not discard a mechanically valid PDF. Downloads use the existing
owner checks. The UI shows actual checked pages and remaining observations.

Qwen comparison has a 600-second budget and 24-page limit in web jobs. Partial
coverage is explicitly reported, including failed page requests. Worker deadlines
include two bounded XeLaTeX passes and this additional review budget. Native Word
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
4. Fill the class with full-width bilingual front matter, two-column body, wide
   objects, attached captions, references, author biographies and license. Keep
   source section intent for structurally conforming reference documents. Numeric
   data and adjoining figure panels in one Word layout table are separated
   semantically while retaining all their original nodes.
5. Use current-article headers, author footer and page start when known. No article
   receives another reference's DOI, publication dates or authors. Use Times New
   Roman when installed, otherwise Liberation Serif; math is TeX Gyre Termes.
6. Compile twice with XeLaTeX. Check missing glyphs, overflow, page bounds,
   extraction coverage and explicit numbering anchors. These gates do not prove
   mathematical equivalence or publication quality.
7. Compare candidate pages with content-aligned reference pages as blind A/B pairs,
   alternating their order. Record exact coverage, raw model remarks and hashes.
   The model's preferences do not automatically edit or promote a document.

The artifact contract and measurements are documented in
[`docs/JAMT_REFERENCE_LAYOUT_20260920.md`](../../docs/JAMT_REFERENCE_LAYOUT_20260920.md).

## Build and limits

Archives contain `main.tex`, `jamt-reference.cls`, assets, font/license, manifest
and layout plan. Compile twice with `xelatex -no-shell-escape main.tex`; TeX Live
and Liberation Serif/DejaVu Sans are required. The application runs the compiler
as its unprivileged account, with restricted file IO and a 180-second timeout per
pass. Uploaded TeX and model-authored commands are never executed.

Supported lists are decimal, level zero, including style-inherited numbering and
start overrides. Unsupported lists, MTEF versions/templates, nested tables,
footnotes/endnotes, tracked deletions, symbolic-font characters and unknown OMML
structures explicitly stop this optional branch. Sparse cells are padded from
declared grid offsets. Table merges preserve text, but not every Word border or
vertical alignment is reproduced. Font metrics and page/column breaks differ
between Word, LibreOffice and XeLaTeX. Do not promise pixel-identical DOCX/PDF
pagination, universal article support or a quality percentage from two examples.

## Tests

```sh
python -m unittest paper_formatter_tests.test_latex_lab paper_formatter_tests.test_mtef \
  apps.template_workspace.test_jamt_reference
python manage.py test apps.template_workspace.test_jamt_style \
  apps.template_workspace.test_jamt_latex_export apps.template_workspace.test_v2_timeouts
```

Real article rendering and inspection are additionally required. The original
dated lab report documents the earlier experiment, not the current export rules.
