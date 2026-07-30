from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from paper_formatter.models import ArticleIR, TemplateProfile
from paper_formatter.semantic.models import SemanticAnalysis, SemanticBlock


class ReportBuilder:
    def render_html(
        self,
        report: dict[str, Any],
        output_path: Path,
        *,
        article: ArticleIR | None = None,
        profile: TemplateProfile | None = None,
        semantic_blocks: list[SemanticBlock] | None = None,
        semantic_analysis: SemanticAnalysis | None = None,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        scores = report.get("scores", {})
        warnings = report.get("warnings", [])
        errors = report.get("errors", [])
        decisions = semantic_analysis.by_id() if semantic_analysis else {}
        audit_rows: list[str] = []
        for block in (semantic_blocks or [])[:1000]:
            decision = decisions.get(block.block_id)
            confidence = f"{decision.confidence:.2f}" if decision else "—"
            audit_rows.append(
                "<tr>"
                f"<td>{html.escape(block.block_id)}</td>"
                f"<td>{html.escape(block.style or '')}</td>"
                f"<td>{html.escape(decision.role if decision else '—')}</td>"
                f"<td>{confidence}</td>"
                f"<td>{html.escape(block.text[:500])}</td>"
                "</tr>"
            )
        title = (
            article.metadata.titles[0].text
            if article and article.metadata.titles
            else "Отчёт преобразования"
        )
        score_cards = "".join(
            self._score_card(name, value)
            for name, value in scores.items()
            if value is not None
        )
        warning_items = "".join(
            f"<li>{html.escape(str(value))}</li>" for value in warnings
        ) or "<li>Нет</li>"
        error_items = "".join(
            f"<li>{html.escape(str(value))}</li>" for value in errors
        ) or "<li>Нет</li>"
        profile_json = html.escape(
            json.dumps(
                profile.model_dump(mode="json") if profile else {},
                ensure_ascii=False,
                indent=2,
            )
        )
        report_json = html.escape(
            json.dumps(report, ensure_ascii=False, indent=2, default=str)
        )
        document = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} — отчёт</title>
<style>
body{{font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#f4f6f8;color:#17202a}}
main{{max-width:1200px;margin:auto;padding:32px}}h1,h2{{line-height:1.2}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.card,section{{background:white;border:1px solid #dce2e8;border-radius:10px;padding:16px;margin:14px 0}}
.score{{font-size:28px;font-weight:700}}.ok{{color:#137333}}.warn{{color:#b06000}}.bad{{color:#b3261e}}
table{{width:100%;border-collapse:collapse;background:white}}th,td{{border:1px solid #dce2e8;padding:7px;vertical-align:top;text-align:left}}
th{{position:sticky;top:0;background:#eef2f6}}.scroll{{max-height:650px;overflow:auto}}
pre{{white-space:pre-wrap;word-break:break-word;background:#101820;color:#e8eef4;padding:14px;border-radius:8px}}
</style>
</head>
<body><main>
<h1>{html.escape(title)}</h1>
<p>Ручная проверка: <strong>{'требуется' if report.get('manual_review_required') else 'не требуется'}</strong></p>
<div class="cards">{score_cards}</div>
<section><h2>Ошибки</h2><ul>{error_items}</ul></section>
<section><h2>Предупреждения</h2><ul>{warning_items}</ul></section>
<section><h2>Аудит семантических блоков</h2>
<div class="scroll"><table><thead><tr><th>ID</th><th>Стиль Word</th><th>Роль</th><th>Уверенность</th><th>Текст</th></tr></thead>
<tbody>{''.join(audit_rows)}</tbody></table></div></section>
<section><h2>TemplateProfile</h2><pre>{profile_json}</pre></section>
<section><h2>Полный JSON-отчёт</h2><pre>{report_json}</pre></section>
</main></body></html>"""
        output_path.write_text(document, encoding="utf-8")
        return output_path

    @staticmethod
    def _score_card(name: str, value: Any) -> str:
        label = html.escape(name.replace("_", " "))
        if isinstance(value, (int, float)):
            numeric = float(value)
            formatted = f"{numeric:.1%}" if 0 <= numeric <= 1 else f"{numeric:g}"
            css = "ok" if numeric >= 0.95 else "warn" if numeric >= 0.80 else "bad"
        else:
            formatted = html.escape(str(value))
            css = ""
        return f'<div class="card"><div>{label}</div><div class="score {css}">{formatted}</div></div>'
