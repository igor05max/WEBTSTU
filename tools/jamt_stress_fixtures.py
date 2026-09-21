"""Independent, editable synthetic manuscripts for JAMT regression review.

These are demonstration manuscripts, not scientific evidence or published work.
They deliberately do not start from the final journal document/template.
"""
from pathlib import Path
import argparse
import json
import random

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt


def paragraph(document, text, *, heading=False):
    p=document.add_paragraph(text)
    p.paragraph_format.space_after=Pt(15 if len(document.paragraphs)%2 else 0)
    p.paragraph_format.first_line_indent=Inches(.4)
    p.alignment=WD_ALIGN_PARAGRAPH.LEFT
    for run in p.runs:
        run.font.name='Calibri' if len(document.paragraphs)%2 else 'Arial'
        run.font.size=Pt(13 if len(document.paragraphs)%3 else 12)
    if heading:p.runs[0].bold=True
    return p


def front(d,title_en,title_ru,abstract_en,abstract_ru,*,missing=False):
    # Metadata values are deliberately pending; the output should mark them.
    paragraph(d,'Original papers')
    paragraph(d,'Advanced structural materials, materials for extreme conditions')
    languages=['ru'] if missing else ['ru','en']  # deliberately reversed
    for lang in languages:
        paragraph(d,title_ru if lang=='ru' else title_en)
        if not missing:
            paragraph(d,'И. П. Петров, А. С. Соколова' if lang=='ru' else 'Ivan P. Petrov, Anna S. Sokolova')
            paragraph(d,'Демонстрационный институт материаловедения, Москва, Российская Федерация' if lang=='ru' else 'Demonstration Institute of Materials Research, Moscow, Russian Federation')
            paragraph(d,'petrov@example.org')
        paragraph(d,('Аннотация' if lang=='ru' else 'Abstract'))
        paragraph(d,abstract_ru if lang=='ru' else abstract_en)
        if not missing:paragraph(d,'Ключевые слова: модель; пористость; теплопередача' if lang=='ru' else 'Keywords: model; porosity; heat transfer')


def equation(d):
    p=d.add_paragraph();math=OxmlElement('m:oMathPara');inner=OxmlElement('m:oMath')
    run=OxmlElement('m:r');text=OxmlElement('m:t');text.text='k=';run.append(text);inner.append(run)
    fraction=OxmlElement('m:f')
    for tag,value in [('m:num','Q·L'),('m:den','A·ΔT')]:
        part=OxmlElement(tag);r=OxmlElement('m:r');t=OxmlElement('m:t');t.text=value;r.append(t);part.append(r);fraction.append(part)
    inner.append(fraction);math.append(inner);p._p.append(math)
    p.add_run('\t(1)')


def references(d):
    paragraph(d,'References',heading=True)
    paragraph(d,'1. Petrov I.P., Sokolova A.S. Demonstration dataset for checking article layout. Unpublished synthetic fixture, 2026.')
    paragraph(d,'2. Laboratory protocol. Repeated measurements of a model specimen. Synthetic data for software verification, 2026.')
    paragraph(d,'Information about the authors',heading=True)
    paragraph(d,'Ivan P. Petrov — demonstration author; petrov@example.org. These names and data belong to a software test manuscript.')
    paragraph(d,'Информация об авторах',heading=True)
    paragraph(d,'Иван Петрович Петров — демонстрационный автор. Имена и данные используются только для проверки оформления.')


def table(d, rows, cols=4):
    t=d.add_table(rows=1, cols=cols);t.style='Table Grid'
    labels=['Specimen','Porosity, %','Conductivity, W/(m·K)','Uncertainty, %','Cycles','Condition'][:cols]
    for cell,label in zip(t.rows[0].cells,labels):cell.text=label
    repeat=OxmlElement('w:tblHeader');t.rows[0]._tr.get_or_add_trPr().append(repeat)
    for i in range(rows):
        values=[f'S-{i+1:02}',f'{12+i*.3:.1f}',f'{.32-i*.001:.3f}',f'{1.5+(i%4)*.1:.1f}',str(10+i),'Dry; stable reading'][:cols]
        for cell,value in zip(t.add_row().cells,values):cell.text=value
    return t


def make(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(5.4,3.3));ax.plot([10,15,20,25,30],[.42,.38,.34,.30,.27],'o-',color='#164a75')
    ax.set(xlabel='Porosity (%)',ylabel='Conductivity (W/(m K))');ax.grid(alpha=.2);fig.tight_layout()
    fig.savefig(output/'porosity.png',dpi=220);plt.close(fig)
    files=[]
    d=Document()
    front(d,'Demonstration study of heat transfer in porous ceramic specimens',
          'Демонстрационное исследование теплопередачи в пористых керамических образцах',
          'This demonstration manuscript checks whether article formatting preserves quantitative information. '
          'A small synthetic dataset describes the influence of porosity on effective thermal conductivity. '
          'The document includes bilingual metadata, a native equation, a table and an illustration. '
          'It does not report a real experiment or support a scientific conclusion.',
          'Демонстрационная рукопись предназначена для проверки сохранности количественных данных при оформлении. '
          'Небольшой искусственный набор описывает связь пористости с эффективной теплопроводностью. '
          'В документ включены сведения на двух языках, формула Word, таблица и иллюстрация. '
          'Приведённые результаты не являются данными реального эксперимента.')
    paragraphs={
        '1. Introduction':[
            'Porous ceramics provide a convenient example of a material whose geometric and thermal characteristics must be reported together. The pore fraction is dimensionless, whereas the conductivity requires a stated measurement direction and a unit. A document formatter must keep these distinctions visible when it changes paragraph widths and page breaks.',
            'The purpose of this synthetic study is to exercise an editorial workflow. Five model specimens have different porosities. Their values were chosen for testing table alignment and decimal preservation. They should not be interpreted as measured properties of a particular material.',
            'An article contains several kinds of structure at once. The abstract describes the scope, the methods specify the procedure, and the captions identify evidence. Good layout makes those relationships easy to follow while retaining the author’s wording.'],
        '2. Materials and methods':[
            'Each model specimen was assigned a code before the demonstration calculations. The labels S-01 through S-05 identify rows of the synthetic dataset. The nominal thickness was 5.0 mm and the temperature difference was 20 K. These values are included to check that the decimal point, the unit and the associated symbol remain unchanged.',
            'The computational example uses a stationary heat-flow relation. In Eq. (1), Q is the heat rate, L is the specimen thickness, A is the cross-sectional area and ΔT is the temperature difference. The formula is stored as native Office Math, rather than as a screenshot.',
            'Three reading cycles were considered for every specimen. A stable reading meant that the variation between two successive cycles did not exceed 0.3 %. This rule is part of the demonstration protocol and is not a proposed metrological standard.'],
        '2.1. Data handling':[
            'All reported values are retained with the precision specified in the source table. Additional digits are not generated during formatting. The uncertainty column is expressed as a percentage, and its values are separate from the conductivity column. A line break inside the header must not make those units appear to belong to a neighbouring column.',
            'The illustrative relationship is monotonic over the selected range. This behaviour was intentionally assigned when creating the fixture. A publication system must not convert that choice into a claim that an experiment confirmed a physical mechanism.'],
        '3. Results and discussion':[
            'Table 1 contains the model values. The identifiers and decimal numbers form a single logical record in each row. The header must remain attached to the first data row, and the caption must remain close to the table even if the object occupies the full page width.',
            'Figure 1 gives a visual summary of the assigned trend. The five markers represent five independent rows; connecting lines only guide the eye. The plot is intentionally accompanied by captions in both languages so that the formatter must retain their association with the same image.',
            'The apparent decrease in conductivity is compatible with the way this synthetic series was constructed. No conclusion about a real material follows from it. The important verification is that the figure, the units and the numerical table survive the production of both Word and PDF.',
            'A document with a longer discussion should continue naturally in the next column. Moving a complete paragraph to obtain a slightly fuller page can change the relationship between a reference to a figure and the figure itself. The test therefore checks the reading order as well as the appearance of each page.'],
        '4. Conclusions':[
            'This synthetic manuscript contains enough structural variety to test a general article formatter without copying a published article. Successful formatting preserves native mathematical objects, table cells and image content while applying the journal’s paragraph and caption styles.',
            'Missing publication identifiers must remain editorial questions. They must be clearly indicated in the output instead of being copied from a reference article. The demonstration is complete only when the Word file remains editable and the PDF preserves the same text and marks.']}
    for heading,body in paragraphs.items():
        paragraph(d,heading,heading=True)
        for text in body:paragraph(d,text)
        if heading=='2. Materials and methods':equation(d)
        if heading=='3. Results and discussion':
            paragraph(d,'Table 1. Synthetic specimen data');paragraph(d,'Таблица 1. Искусственные данные образцов');table(d,5)
            p=d.add_paragraph();p.add_run().add_picture(str(output/'porosity.png'),width=Inches(4.6))
            paragraph(d,'Fig. 1. Assigned conductivity as a function of porosity')
            paragraph(d,'Рис. 1. Заданная зависимость теплопроводности от пористости')
    references(d);p=output/'bilingual-objects.docx';d.save(p);files.append(p.name)
    d=Document();front(d,'','Проверка неполной рукописи с редакционными ошибками','',
        'Эта демонстрационная рукопись намеренно содержит пропуски и подозрительные символы. '
        'Система должна показать места для проверки, сохранив каждое исходное значение.',missing=True)
    for text in ['1. Введение','В этой этой работе измерена прoчность образца. Результат: 12.5 ± 0.3 МПа.',
                 'В исходном файле остался символ \ufffd, а также невидимый раз\u200bрыв слова. Эти места требуют проверки.',
                 '2. Методика','Температуру поддерживали на уровне 25 °C. Состав Ti6Al4V и обозначение β-SiC нельзя исправлять как опечатки.',
                 '3. Результаты','В таблице черновика записано значение 1,25.6. Оно неоднозначно и должно быть выделено, а не заменено догадкой.',
                 'Заключение','Недостающие сведения об авторах, организациях и переводе не должны появляться из опубликованного образца.',
                 'Received 31.02.2026','References','1. Демонстрационная запись для проверки ссылок. 2026.']:
        paragraph(d,text,heading=text.startswith(('1. В','2. М','3. Р','Заключение','References')))
    p=output/'incomplete-russian.docx';d.save(p);files.append(p.name)
    d=Document();front(d,'Verification of a long measurement table','Проверка длинной таблицы измерений',
        'A synthetic table with 48 records checks repeated headers, row integrity and page continuation. Values are invented solely for layout testing.',
        'Искусственная таблица из 48 записей проверяет повторение шапки, целостность строк и переходы между страницами. Значения придуманы исключительно для испытания вёрстки.')
    paragraph(d,'1. Introduction',heading=True);paragraph(d,'The table is larger than one page. It must continue with the same column widths and repeat its header without losing, duplicating or merging data rows.')
    paragraph(d,'2. Dataset',heading=True);paragraph(d,'All specimen codes are unique. The final record is S-48. The document is a software fixture, not an experimental report.')
    paragraph(d,'Table 1. Synthetic data for checking page continuation');table(d,48,6)
    paragraph(d,'3. Conclusions',heading=True);paragraph(d,'All 48 records and their six columns must appear in both formats. A continued table should remain readable at the normal journal font size.');references(d)
    p=output/'long-table.docx';d.save(p);files.append(p.name)
    d=Document();front(d,'Native mathematics and structured procedures','Нативные формулы и структурированные процедуры',
        'The fixture checks mathematical objects, automatic lists and a table with merged headings. No scientific result is claimed.',
        'Рукопись проверяет математические объекты, автоматические списки и таблицу с объединёнными заголовками. Научные результаты не заявляются.')
    paragraph(d,'1. Introduction',heading=True);paragraph(d,'The expression below is native Word mathematics. Its numerator and denominator must remain editable in Word and correctly typeset in LaTeX.');equation(d)
    paragraph(d,'2. Procedure',heading=True)
    for text in ['Assign a unique specimen identifier.','Record the nominal thickness and the measurement direction.','Repeat the reading and retain its original precision.']:
        d.add_paragraph(text,style='List Number')
    for text in ['The decimal point is part of the value.','The uncertainty must not become a footnote marker.','Greek letters and units are meaningful symbols.']:
        d.add_paragraph(text,style='List Bullet')
    paragraph(d,'Table 1. Grouped synthetic properties')
    t=d.add_table(rows=5,cols=4);t.style='Table Grid';t.cell(0,0).merge(t.cell(1,0)).text='Material';t.cell(0,1).merge(t.cell(0,2)).text='Thermal properties';t.cell(0,3).merge(t.cell(1,3)).text='Condition'
    t.cell(1,1).text='k, W/(m·K)';t.cell(1,2).text='c, J/(kg·K)'
    for i in range(2,5):
        for cell,text in zip(t.rows[i].cells,[f'M-{i-1}',f'{.4+i*.1:.2f}',str(700+i*10),'Dry']):cell.text=text
    paragraph(d,'3. Conclusions',heading=True);paragraph(d,'The grouped header expresses a hierarchy. Its merged cells must retain their labels and boundaries. Automatic numbering is visible content, even though the numbers are not stored as ordinary text runs.');references(d)
    p=output/'math-lists-merged.docx';d.save(p);files.append(p.name)
    (output/'manifest.json').write_text(json.dumps({'synthetic':True,'seed':0,'files':files},indent=2),encoding='utf-8')
    print(json.dumps(files))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output');args=parser.parse_args();make(args.output)
