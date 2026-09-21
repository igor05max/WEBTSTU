"""Run the real native editor and LaTeX bridge on independently authored drafts."""
import argparse
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from apps.template_workspace.v2.editorial import annotate_manuscript
from apps.template_workspace.v2.classification.roles import RoleClassifierV2
from apps.template_workspace.v2.inspector.document import DocumentInspector
from apps.template_workspace.v2.styles.jamt import prepare_jamt_style
from apps.template_workspace.v2.editor.safe_word_editor import SafeWordEditor
from paper_formatter.latex_lab.bridge import NativeBridge
from paper_formatter.latex_lab.typesetter import default_plan, render, compile_pdf
from paper_formatter.latex_lab.quality import measure, gate


def build(source, output, compile_tex=False):
    output.mkdir(parents=True,exist_ok=True)
    reviewed=output/'reviewed.docx'
    review=annotate_manuscript(source,reviewed)
    (output/'editorial-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
    report=DocumentInspector(reviewed).inspect();structure=RoleClassifierV2(use_ai=False).article_structure(report)
    template,tr,profile=prepare_jamt_style(output,article_report=report,article_structure=structure)
    native=SafeWordEditor().render(article_path=reviewed,template_path=template,output_path=output/'result.docx',
        article_report=report,article_structure=structure,template_report=tr,template_profile=profile)
    (output/'editor-report.json').write_text(json.dumps(native.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'structure.json').write_text(json.dumps(structure.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
    blocks,manifest=NativeBridge(output/'result.docx',output/'latex').build()
    plan=default_plan(blocks,**{key:manifest[key] for key in ('running_footer','running_header','page_start','reference_layout')})
    render(blocks,output/'latex',plan)
    result={'name':source.name,'native_integrity':native.metrics.get('native_integrity'),
            'missing':review['missing_count'],'findings':review['highlighted_findings'],
            'text_transfer':manifest['text_transfer'],'objects':len(manifest['assets']),
            'equations':len(manifest['formulas']),'breakable_tables':[b.id for b in blocks if b.breakable]}
    if compile_tex:
        pdf,compiled=compile_pdf(output/'latex');metrics=measure(pdf,manifest,compiled)
        result.update(latex=metrics,gate=gate(metrics,metrics))
    (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('output');parser.add_argument('--compile',action='store_true')
    args=parser.parse_args();source=Path(args.source);out=Path(args.output)
    sources=sorted(source.glob('*.docx')) if source.is_dir() else [source]
    for path in sources:
        try:print(json.dumps(build(path,out/path.stem,args.compile),ensure_ascii=False),flush=True)
        except Exception:traceback.print_exc();sys.exit(1)
