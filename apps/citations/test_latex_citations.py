from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings

from apps.citations.workspaces import apply_to_latex, create_workspace


SOURCE = br"""
\documentclass{article}
\begin{document}
\section{Introduction}
Response quality is difficult to evaluate in technical question answering.
\begin{thebibliography}{9}
\bibitem{existing} Existing source.
\end{thebibliography}
\end{document}
"""


class LatexCitationInsertionTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.override = override_settings(CITATION_WORKSPACE_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.directory.cleanup)

    def _workspace(self, claim_text):
        claims = [
            {
                "id": "claim-1",
                "text": claim_text,
                "paragraph_index": 2,
                "recommendations": [
                    {
                        "article_id": "article-1",
                        "title": "Evaluation of grounded answers",
                        "citation": "Author A. Evaluation of grounded answers. 2026.",
                    }
                ],
            }
        ]
        return create_workspace(
            user_id=7,
            file_bytes=SOURCE,
            file_name="ARTICLE.tex",
            snapshot={"text": claim_text},
            claims=claims,
            index_status={},
        )

    def test_selected_source_adds_cite_marker_and_bibitem(self):
        payload = self._workspace(
            "Response quality is difficult to evaluate in technical question answering."
        )

        output, name = apply_to_latex(
            user_id=7,
            token=payload["token"],
            selections=[{"claim_id": "claim-1", "article_id": "article-1"}],
        )
        source = output.getvalue().decode("utf-8")

        self.assertEqual(name, "ARTICLE_with_citations.tex")
        self.assertIn(r"answering. \cite{rag2}", source)
        self.assertIn(r"\bibitem{rag2} Author A. Evaluation of grounded answers. 2026.", source)

    def test_document_is_not_created_when_claim_marker_cannot_be_inserted(self):
        payload = self._workspace("A sentence that does not exist in the source.")

        with self.assertRaisesMessage(ValueError, "Документ не создан"):
            apply_to_latex(
                user_id=7,
                token=payload["token"],
                selections=[{"claim_id": "claim-1", "article_id": "article-1"}],
            )
