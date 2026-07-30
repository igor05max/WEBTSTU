from paper_formatter.config import SemanticSettings
from paper_formatter.semantic.classifier import HybridSemanticClassifier
from paper_formatter.semantic.models import SemanticBlock
from paper_formatter.semantic.rules import RuleSemanticClassifier


def test_numbered_heading_uses_explicit_hierarchy():
    blocks = [
        SemanticBlock(
            block_id="b-1",
            order=1,
            text="2.1. Методика эксперимента",
            style="Heading 3",
            bold_ratio=1.0,
            numbered_prefix="2.1",
        )
    ]
    analysis = RuleSemanticClassifier().analyze_document(
        blocks,
        document_name="a.docx",
    )
    decision = analysis.decisions[0]
    assert decision.role == "subsection"
    assert decision.heading_level == 2


def test_numbered_sequence_is_list():
    blocks = [
        SemanticBlock(
            block_id=f"b-{index}",
            order=index,
            text=f"{index}. Шаг {index}",
            style="Source Code",
            numbered_prefix=str(index),
            is_in_numbered_sequence=True,
        )
        for index in range(1, 5)
    ]
    analysis = RuleSemanticClassifier().analyze_document(
        blocks,
        document_name="a.docx",
    )
    assert all(item.role == "list_item" for item in analysis.decisions)


def test_web_formatter_is_deterministic_and_rules_only():
    classifier = HybridSemanticClassifier(SemanticSettings())
    result = classifier.analyze_document(
        [
            SemanticBlock(
                block_id="b-1",
                order=1,
                text="Название статьи",
                style="Title",
            ),
            SemanticBlock(
                block_id="b-2",
                order=2,
                text="Обухов Артём Дмитриевич",
                style="Normal",
            ),
        ],
        document_name="a.docx",
    )

    assert result.provider == "rules-only"
    assert result.by_id()["b-2"].role == "author"
