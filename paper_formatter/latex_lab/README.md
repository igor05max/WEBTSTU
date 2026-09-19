# JAMT LaTeX laboratory

Experimental CLI for comparing the native Word renderer with a second typesetter.
It is intentionally not connected to web uploads or enabled in production jobs.
No fine-tuning is performed: Qwen supplies bounded layout advice and visual critique.

```sh
DJANGO_SETTINGS_MODULE=config.settings python -m paper_formatter.latex_lab article.docx \
  --output /path/to/experiment --search --qwen
```

Use `--baseline-docx result.docx --baseline-pdf result.pdf` to compare against a
previously verified native result. `--reuse` retains that native baseline when
iterating on the experimental renderer. Inputs must be DOCX; the site's DOC
conversion is not invoked by this CLI.

## Pipeline

1. Run the existing JAMT native editor (or retain the supplied native baseline).
   This remains the editable DOCX companion. It is **not** a LaTeX-to-Word round trip.
2. Read native paragraphs, table cells, drawings, text boxes, and Office Math.
   Preserve the bilingual front, references, author information, run emphasis,
   superscripts/subscripts, table grids, and current-article footer text.
3. Escape all manuscript text. Copy original image bytes and reproduce Word crop,
   mirror, and rotation settings in separate derived assets. Record both assets.
4. Convert supported OMML structures to mathematical LaTeX. Formula text is never
   invented by Qwen. Count formulas and record original XML hashes and generated math.
5. Compare every native visible text node with the text nodes sent to the renderer.
   Missing or duplicated fragments abort export. A separate PDF text check catches
   rendering regressions, but is not a proof of mathematical equivalence.
6. Generate the fixed JAMT TeX structure. XeLaTeX handles line breaking, language
   hyphenation, math, microtype protrusion, column balancing, and table construction.
   The supplied open-license TeX Gyre math font avoids machine-dependent math fonts.
7. Optionally compile three bounded configurations. Body remains 11 pt and
   tables/captions 10 pt; leading varies 12.1–12.65 pt, image scale 95–100%.
   A heuristic ranks whitespace and overflow only among candidates passing gates.
8. Optionally ask Qwen for a JSON layout patch. The application validates object
   identifiers, scalar ranges, enums, and geometry constraints, then compiles and
   evaluates a separate candidate. Worse candidates are rejected.
9. Compare selected native/LaTeX page regions as anonymized A/B pairs with alternating
   ordering. This is **sampled** visual review by the same model family as the planner,
   not an independent comprehensive evaluation. Manual review remains necessary.

## Output and interpretation

- `native/result.docx`, `native/result.pdf`: unchanged baseline/Word companion.
- `latex/main.tex`, `latex/main.pdf`: best bounded-search candidate.
- `latex_qwen/`: optional Qwen proposal; may be rejected. Read `selected_project`.
- `latex/manifest.json`: text transfer, formulas, original image hashes/transforms.
- `comparison.json`: all candidate metrics, gates, and selection, without publication.
- `qwen_plan_report.json`, `qwen_comparison.json`: advice and actual sampled coverage.

PDF text coverage is an extraction diagnostic. Hyphenation, superscripts, symbols,
and cells can change extraction tokenization without changing visible content.
The DOCX and LaTeX PDF carry the same manuscript but can have different pagination.
Do not present the DOCX as an editable, page-identical version of the LaTeX PDF.

For the two benchmark case directories (`balabanov` and `tyutyunnik`):

```sh
python -m paper_formatter.latex_lab.package /path/to/experiment-root /path/to/delivery
```

This recompiles the selected projects, checks the final PDFs, and packages four
PDFs, two native DOCX companions, two buildable TeX archives, metrics and a portable
`index.html` comparison. Its page/zoom controls use raster previews, so an embedded
browser PDF plug-in is not required. The report starts with visual review pending;
record an actual page-by-page agent review separately, including the PDF hash and
remaining observations. Agent review does not imply human/editorial approval.

## Explicit limits

The bridge fails on unsupported OLE/MathType, metafiles, nested/sparse tables,
footnotes/endnotes, unresolved tracked deletions, unrecognized symbolic-font
characters, and unsupported Office Math constructs. It does not rasterize unknown
equations or silently drop their contents. Vertically merged table text is retained
in its originating cell, but the lab does not reproduce every Word border/alignment.

No uploaded TeX is executed. Generated sources are escaped, commands come from code,
the compiler runs without shell escape and with restricted file IO, and Qwen cannot
provide TeX, document text, filesystem paths, or arbitrary operations. Use the
application account and a private output directory, not root.

## Verification

```sh
python -m unittest paper_formatter_tests.test_latex_lab -v
```

Tests cover literal untrusted text, image crop with untouched originals, duplicate
AlternateContent, supported and rejected math, table-cell boundaries, merged-cell
geometry, model constraints, and failure gates. Real-article rendering and full-page
visual review are additionally required. See the dated experiment report in `docs/`.

Typesetting references: [multicol](https://ctan.org/pkg/multicol) and
[microtype](https://ctan.org/pkg/microtype). XeLaTeX supports microtype protrusion;
the experiment does not claim pdfTeX/LuaTeX-only font expansion.
