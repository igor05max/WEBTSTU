# JAMT 2026.3: raw manuscript regression

The finished Word references are fidelity fixtures, not substitutes for testing
unformatted input. A raw manuscript beginning with a standalone inline portrait
exposed a front/body boundary error: its textless picture remained in the front,
preventing language ordering and affiliation/abstract merging.

The editor now groups a large inline picture with its immediately following
body caption. A genuine front logo still prevents unsafe front reordering.
Caption group IDs survive this step so translated captions remain inside the
same full-width span. `Fig 5.` is a caption; `Fig 5 presents …` remains prose.

Detached native caption boxes use the saved caption typography in both modern
and legacy OOXML representations. Text-only caption frames occupy the available
column width, preventing LibreOffice from left-pinning a narrow frame that Word
centres. Images, OLE previews, license boxes and mixed drawing groups are not
stretched. The structural quality check now inspects nested caption font sizes.
Trusted JAMT running lines retain the profile's black colour.
Consecutive translated rubric labels share one inter-block gap, instead of
adding a full paragraph gap after each language. This keeps the draft's last
front-matter citation on the opening page in the server PDF.

Validation includes the actual raw Tyutyunnik manuscript, Word and LibreOffice
renders, unchanged image/OLE bytes, and two finished Word references preserved
byte for byte. Synthetic tests cover leading portraits, genuine front logos,
bilingual wide captions, text boxes, source licenses and caption/prose ambiguity.

The draft and final editorial article are different text revisions. This fix
preserves the draft's red/yellow editorial annotations, placeholders, science
and bibliographic text. It does not infer publication identifiers, dates,
license wording or revised author facts from another file. A six-page result
or an AI review with no findings is not proof of perfect editorial layout.
