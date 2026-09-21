"""Position the first rubric independently of the ordinary 30 mm body margin."""
from lxml import etree
from ..ooxml.namespaces import NS, qn
from .jamt import load_style


def position_front_labels(body, metadata):
    paragraphs = [p for p in body if p.tag == qn('w:p') and ''.join(p.xpath('.//w:t/text()', namespaces=NS)).strip()]
    if len(paragraphs) < 2 or not metadata.get(paragraphs[0]) or metadata[paragraphs[0]].role != 'article_type':
        return 0
    labels = [paragraphs[0]]
    for p in paragraphs[1:]:
        if not metadata.get(p) or metadata[p].role != 'rubric': break
        labels.append(p)
    if len(labels) < 2 or any(p.xpath('.//w:drawing|.//w:pict|./w:pPr/w:sectPr', namespaces=NS) for p in labels):
        return 0
    style = load_style()
    # Identical native frames are one auto-height group in Word. No text box,
    # fixed height, image conversion or source-text reconstruction is needed.
    for i,p in enumerate(labels):
        pr = p.find('w:pPr', NS)
        frame = pr.find('w:framePr', NS)
        if frame is None: frame = etree.SubElement(pr, qn('w:framePr'))
        for key,value in {'w':style['roles']['editorial_metadata']['right_tab'],
                          'hAnchor':'page','vAnchor':'page','x':style['margins_twips']['left'],
                          'y':style['furniture']['front_frame_y_twips'],
                          'wrap':'notBeside','hRule':'auto'}.items():
            frame.set(qn('w:'+key), str(value))
        spacing = pr.find('w:spacing', NS)
        spacing.set(qn('w:after'), str(round(style['roles']['rubric']['after']*20)) if i == len(labels)-1 else '0')
    return len(labels)
