import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from apps.conclusions.models import ConclusionDocument, ConclusionSignature
from apps.submissions.document_preview import DocumentPreviewError, convert_word_path_to_pdf
from apps.workflow.models import AssigneeKind, WorkflowStep


PRORECTOR_ROLE_NAME = "Проректор по научной работе"
PRORECTOR_STEP_NAME = "Утверждение проректором по научной работе"
SIGNATURE_BLUE = (58 / 255, 105 / 255, 246 / 255)
SIGNATURE_FONT_NAME = "ConclusionSignatureFont"
SIGNATURE_XML_NAMESPACE = "urn:tgtu:electronic-document:1.0"


class ConclusionGenerationError(ValueError):
    pass


def calculate_file_sha256(file_field):
    digest = hashlib.sha256()
    with file_field.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get_signer_name(user):
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _get_short_author_names(submission):
    authors = submission.authors.order_by("last_name", "first_name", "username")
    names = []
    for author in authors:
        if author.last_name:
            initials = "".join(f"{value[:1]}." for value in (author.first_name,) if value)
            names.append(f"{author.last_name} {initials}".strip())
        else:
            names.append(_get_signer_name(author))
    return ", ".join(names) or _get_signer_name(submission.author)


def _replace_template_values(template_bytes, values):
    source = io.BytesIO(template_bytes)
    target = io.BytesIO()
    replacements = {str(key): str(value) for key, value in values.items()}

    with ZipFile(source, "r") as archive, ZipFile(target, "w", ZIP_DEFLATED) as result:
        for item in archive.infolist():
            content = archive.read(item.filename)
            if item.filename == "word/document.xml":
                text = content.decode("utf-8")
                for old_value, new_value in replacements.items():
                    text, _replaced = _replace_word_text_token(text, old_value, new_value)
                if "{{" in text or "}}" in text:
                    raise ConclusionGenerationError(
                        "В шаблоне заключения остались незаполненные обязательные поля."
                    )
                content = text.encode("utf-8")
            result.writestr(item, content)
    return target.getvalue()


def _replace_word_text_token(xml_text, token, replacement):
    word_namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    for prefix, namespace in re.findall(r'xmlns:([^=\s]+)="([^"]+)"', xml_text):
        ElementTree.register_namespace(prefix, namespace)
    root = ElementTree.fromstring(xml_text)
    text_nodes = list(root.iter(f"{{{word_namespace}}}t"))
    replacement_count = 0

    while True:
        node_texts = [node.text or "" for node in text_nodes]
        combined = "".join(node_texts)
        start = combined.find(token)
        if start < 0:
            break
        end = start + len(token)

        offset = 0
        start_node_index = end_node_index = None
        start_offset = end_offset = None
        for index, value in enumerate(node_texts):
            next_offset = offset + len(value)
            if start_node_index is None and start < next_offset:
                start_node_index = index
                start_offset = start - offset
            if end <= next_offset:
                end_node_index = index
                end_offset = end - offset
                break
            offset = next_offset

        if start_node_index is None or end_node_index is None:
            raise ConclusionGenerationError("Не удалось заполнить поле в Word-шаблоне.")

        if start_node_index == end_node_index:
            current = node_texts[start_node_index]
            text_nodes[start_node_index].text = (
                current[:start_offset] + replacement + current[end_offset:]
            )
        else:
            first_text = node_texts[start_node_index]
            last_text = node_texts[end_node_index]
            text_nodes[start_node_index].text = first_text[:start_offset] + replacement
            for index in range(start_node_index + 1, end_node_index):
                text_nodes[index].text = ""
            text_nodes[end_node_index].text = last_text[end_offset:]
        replacement_count += 1

    return (
        ElementTree.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8"),
        replacement_count,
    )


def _build_conclusion_docx(document):
    template_path = Path(settings.CONCLUSION_TEMPLATE_PATH)
    if not template_path.is_file():
        raise ConclusionGenerationError(
            f"Не найден шаблон заключения: {template_path}."
        )

    values = {
        "{{publication_name}}": document.submission.title,
        "{{short_fio}}": _get_short_author_names(document.submission),
        "рег. №_________________": f"рег. №{document.registration_number}",
    }
    return _replace_template_values(template_path.read_bytes(), values)


def get_or_create_prorector_role():
    role, _created = Group.objects.get_or_create(name=PRORECTOR_ROLE_NAME)
    return role


def append_prorector_approval_step(workflow_run, *, after_order):
    prorector_role = get_or_create_prorector_role()
    existing_step = workflow_run.steps.filter(assigned_group=prorector_role).first()
    if existing_step is not None:
        return existing_step
    return WorkflowStep.objects.create(
        workflow_run=workflow_run,
        step_template=None,
        order=after_order + 1,
        name=PRORECTOR_STEP_NAME,
        assignee_kind=AssigneeKind.FIXED_GROUP,
        assigned_group=prorector_role,
        assigned_unit=None,
        assigned_user=None,
        can_reject=False,
        can_request_revision=False,
    )


@transaction.atomic
def ensure_conclusion_document(workflow_run):
    existing = ConclusionDocument.objects.filter(workflow_run=workflow_run).first()
    if existing is not None:
        return existing

    submission = workflow_run.submission
    if submission.current_version_id is None:
        raise ConclusionGenerationError(
            "Нельзя сформировать заключение без текущей версии рукописи."
        )

    document = ConclusionDocument.objects.create(
        workflow_run=workflow_run,
        submission=submission,
        source_version=submission.current_version,
        registration_number=f"pending-{uuid.uuid4()}",
    )
    document.registration_number = (
        f"{settings.CONCLUSION_REGISTRATION_PREFIX}-{timezone.localdate().year}-{document.pk:05d}"
    )
    docx_bytes = _build_conclusion_docx(document)
    docx_filename = f"{document.registration_number}.docx"

    document.document_file.save(docx_filename, ContentFile(docx_bytes), save=False)
    document.document_sha256 = hashlib.sha256(docx_bytes).hexdigest()
    document.sealed_at = timezone.now()
    document.is_sealed = True
    document.save()
    return document


def _build_event_hash(previous_event_hash, payload):
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(
        f"{previous_event_hash}|{serialized}".encode("utf-8")
    ).hexdigest()


def create_authenticated_signature(task, actor, decision, *, request_meta=None):
    workflow_run = task.workflow_step.workflow_run
    document = ConclusionDocument.objects.filter(workflow_run=workflow_run).first()
    if document is None:
        return None
    if ConclusionSignature.objects.filter(task=task).exists():
        raise ValueError("Подпись по этой задаче уже зафиксирована.")

    submission = workflow_run.submission
    version = submission.current_version
    if version is None:
        raise ValueError("Не найдена версия рукописи для подписания.")

    request_meta = request_meta or {}
    previous_signature = document.signatures.order_by("-signed_at", "-id").first()
    previous_event_hash = previous_signature.event_hash if previous_signature else ""
    signed_at = timezone.now()
    signer_role = task.assigned_group.name if task.assigned_group_id else "Персональное назначение"
    payload = {
        "operation_id": str(uuid.uuid4()),
        "document_id": document.id,
        "registration_number": document.registration_number,
        "document_sha256": document.document_sha256,
        "submission_id": submission.id,
        "submission_version_id": version.id,
        "submission_version_number": version.version_number,
        "submission_version_sha256": calculate_file_sha256(version.file),
        "task_id": task.id,
        "decision_id": decision.id,
        "decision": decision.decision,
        "signer_id": actor.id,
        "signer_name": _get_signer_name(actor),
        "signer_role": signer_role,
        "confirmation_method": "authenticated_session",
        "signed_at": signed_at.isoformat(),
    }
    operation_id = payload["operation_id"]
    return ConclusionSignature.objects.create(
        document=document,
        task=task,
        decision=decision,
        signer=actor,
        signer_name=payload["signer_name"],
        signer_role=signer_role,
        submission_version=version,
        submission_version_number=version.version_number,
        submission_version_sha256=payload["submission_version_sha256"],
        document_sha256=document.document_sha256,
        operation_id=operation_id,
        client_ip=request_meta.get("client_ip"),
        user_agent=request_meta.get("user_agent", "")[:1000],
        signed_payload=payload,
        previous_event_hash=previous_event_hash,
        event_hash=_build_event_hash(previous_event_hash, payload),
        signed_at=signed_at,
    )


def verify_conclusion_document(document):
    current_hash = calculate_file_sha256(document.document_file)
    result = {
        "is_valid": current_hash == document.document_sha256,
        "expected_sha256": document.document_sha256,
        "actual_sha256": current_hash,
    }
    package_fields = (
        ("source_pdf", document.source_pdf_file, document.source_pdf_sha256),
        ("printed_pdf", document.printed_pdf_file, document.printed_pdf_sha256),
        ("signature_data", document.signature_data_file, document.signature_data_sha256),
    )
    package_validity = {}
    for key, file_field, expected_hash in package_fields:
        if not file_field or not expected_hash:
            package_validity[key] = None
            continue
        actual_hash = calculate_file_sha256(file_field)
        package_validity[key] = {
            "is_valid": actual_hash == expected_hash,
            "expected_sha256": expected_hash,
            "actual_sha256": actual_hash,
        }
    result["package"] = package_validity
    result["package_is_valid"] = bool(document.package_finalized_at) and all(
        item and item["is_valid"] for item in package_validity.values()
    )
    return result


def _sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


def _convert_conclusion_docx_to_pdf(document):
    with tempfile.TemporaryDirectory(prefix="conclusion-package-") as temporary_directory:
        temporary_directory = Path(temporary_directory)
        source_path = temporary_directory / "conclusion.docx"
        output_path = temporary_directory / "conclusion.pdf"
        try:
            with document.document_file.open("rb") as source, source_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            convert_word_path_to_pdf(source_path, output_path, format_name="DOCX")
            pdf_bytes = output_path.read_bytes()
        except (DocumentPreviewError, OSError) as exc:
            raise ConclusionGenerationError(
                "Не удалось сформировать PDF-комплект заключения. Проверьте конвертер Word/LibreOffice."
            ) from exc
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ConclusionGenerationError("Конвертер вернул некорректный PDF заключения.")
    return pdf_bytes


def _resolve_signature_font_path():
    configured_path = str(getattr(settings, "CONCLUSION_PDF_FONT_PATH", "") or "").strip()
    candidates = [
        configured_path,
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise ConclusionGenerationError(
        "Не найден шрифт с поддержкой кириллицы для визуализации электронных подписей."
    )


def _get_signature_font_name():
    if SIGNATURE_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(SIGNATURE_FONT_NAME, str(_resolve_signature_font_path())))
    return SIGNATURE_FONT_NAME


def _split_word_to_width(word, *, font_name, font_size, max_width):
    chunks = []
    current = ""
    for character in word:
        candidate = current + character
        if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
            chunks.append(current)
            current = character
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


def _wrap_pdf_text(value, *, font_name, font_size, max_width):
    lines = []
    for source_line in str(value or "").splitlines() or [""]:
        words = source_line.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            word_chunks = _split_word_to_width(
                word,
                font_name=font_name,
                font_size=font_size,
                max_width=max_width,
            )
            for chunk in word_chunks:
                candidate = f"{current} {chunk}".strip()
                if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
                    lines.append(current)
                    current = chunk
                else:
                    current = candidate
        if current:
            lines.append(current)
    return lines or [""]


def _signature_status(index, signature_count):
    return "Подписано" if index == signature_count - 1 else "Согласовано"


def _format_timezone_offset(value):
    offset = value.utcoffset()
    if offset is None:
        return "GMT"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"GMT{sign}{hours:02d}:{minutes:02d}"


def _signature_row_data(signature, *, index, signature_count, font_name, column_widths):
    signed_at = timezone.localtime(signature.signed_at)
    signer_lines = _wrap_pdf_text(
        signature.signer_name,
        font_name=font_name,
        font_size=8.8,
        max_width=column_widths[0] - 18,
    )
    signer_lines.extend(
        _wrap_pdf_text(
            signature.signer_role,
            font_name=font_name,
            font_size=8.4,
            max_width=column_widths[0] - 18,
        )
    )
    certificate_lines = [
        "ПЭП ТГТУ",
        str(signature.operation_id),
        _signature_status(index, signature_count),
    ]
    date_lines = [
        signed_at.strftime("%d.%m.%Y %H:%M:%S"),
        _format_timezone_offset(signed_at),
    ]
    line_counts = (len(signer_lines), len(certificate_lines), len(date_lines))
    row_height = max(58, max(line_counts) * 10 + 20)
    return signer_lines, certificate_lines, date_lines, row_height


def _draw_lines(pdf_canvas, lines, *, x, top, font_name, font_size, leading):
    pdf_canvas.setFont(font_name, font_size)
    y = top
    for line in lines:
        pdf_canvas.drawString(x, y, line)
        y -= leading


def _draw_signature_panel(pdf_canvas, *, page_width, page_height, document, signatures, bottom):
    font_name = _get_signature_font_name()
    margin = 22
    panel_width = page_width - 2 * margin
    column_widths = (panel_width * 0.26, panel_width * 0.46, panel_width * 0.28)
    rows = [
        _signature_row_data(
            signature,
            index=index,
            signature_count=len(signatures),
            font_name=font_name,
            column_widths=column_widths,
        )
        for index, signature in enumerate(signatures)
    ]
    title_height = 34
    header_height = 42
    panel_height = title_height + header_height + sum(row[3] for row in rows)
    top = bottom + panel_height
    if top > page_height - 24:
        return False

    pdf_canvas.setStrokeColorRGB(*SIGNATURE_BLUE)
    pdf_canvas.setFillColorRGB(*SIGNATURE_BLUE)
    pdf_canvas.setLineWidth(0.8)
    pdf_canvas.rect(margin, bottom, panel_width, panel_height, stroke=1, fill=0)

    title = f"Заключение {document.registration_number} подписано в системе ЭДО ТГТУ"
    title_lines = _wrap_pdf_text(
        title,
        font_name=font_name,
        font_size=9.2,
        max_width=panel_width - 24,
    )
    title_y = top - 15
    for line in title_lines:
        pdf_canvas.setFont(font_name, 9.2)
        pdf_canvas.drawCentredString(page_width / 2, title_y, line)
        title_y -= 11

    x_positions = (
        margin + 14,
        margin + column_widths[0] + 10,
        margin + column_widths[0] + column_widths[1] + 10,
    )
    header_top = top - title_height - 13
    _draw_lines(
        pdf_canvas,
        ["Подписант", "(должность, ФИО)"],
        x=x_positions[0],
        top=header_top,
        font_name=font_name,
        font_size=8.2,
        leading=9,
    )
    _draw_lines(
        pdf_canvas,
        ["Сертификат", "(тип, идентификатор, результат)"],
        x=x_positions[1],
        top=header_top,
        font_name=font_name,
        font_size=8.2,
        leading=9,
    )
    _draw_lines(
        pdf_canvas,
        ["Дата и время подписания"],
        x=x_positions[2],
        top=header_top,
        font_name=font_name,
        font_size=8.2,
        leading=9,
    )

    current_top = top - title_height - header_height
    pdf_canvas.line(margin + 14, current_top, margin + panel_width - 14, current_top)
    for signer_lines, certificate_lines, date_lines, row_height in rows:
        text_top = current_top - 17
        _draw_lines(
            pdf_canvas,
            signer_lines,
            x=x_positions[0],
            top=text_top,
            font_name=font_name,
            font_size=8.4,
            leading=9.5,
        )
        _draw_lines(
            pdf_canvas,
            certificate_lines,
            x=x_positions[1],
            top=text_top,
            font_name=font_name,
            font_size=8.4,
            leading=9.5,
        )
        _draw_lines(
            pdf_canvas,
            date_lines,
            x=x_positions[2],
            top=text_top,
            font_name=font_name,
            font_size=8.4,
            leading=9.5,
        )
        current_top -= row_height
        if current_top > bottom + 1:
            pdf_canvas.line(margin + 14, current_top, margin + panel_width - 14, current_top)
    return True


def _signature_overlay_pdf(*, page_width, page_height, document, signatures, bottom):
    buffer = io.BytesIO()
    pdf_canvas = canvas.Canvas(buffer, pagesize=(page_width, page_height), invariant=1)
    fits = _draw_signature_panel(
        pdf_canvas,
        page_width=page_width,
        page_height=page_height,
        document=document,
        signatures=signatures,
        bottom=bottom,
    )
    pdf_canvas.save()
    return buffer.getvalue(), fits


def _page_has_text_in_signature_area(page, *, bottom, page_height):
    positions = []

    def collect_text(text, _cm, text_matrix, _font_dictionary, _font_size):
        if text and text.strip():
            positions.append(float(text_matrix[5]))

    try:
        page.extract_text(visitor_text=collect_text)
    except Exception:
        return True
    return any(bottom + 6 < y < page_height - 20 for y in positions)


def _build_printed_pdf(source_pdf_bytes, document, signatures):
    if not signatures:
        raise ConclusionGenerationError("Нельзя сформировать печатную форму без подписей.")
    try:
        reader = PdfReader(io.BytesIO(source_pdf_bytes))
    except Exception as exc:
        raise ConclusionGenerationError("Не удалось прочитать PDF заключения.") from exc
    if not reader.pages:
        raise ConclusionGenerationError("PDF заключения не содержит страниц.")

    last_page = reader.pages[-1]
    page_width = float(last_page.mediabox.width)
    page_height = float(last_page.mediabox.height)
    overlay_bytes, fits = _signature_overlay_pdf(
        page_width=page_width,
        page_height=page_height,
        document=document,
        signatures=signatures,
        bottom=158,
    )
    writer = PdfWriter()
    can_overlay_last_page = fits and not _page_has_text_in_signature_area(
        last_page,
        bottom=158,
        page_height=page_height,
    )
    if can_overlay_last_page:
        last_page.merge_page(PdfReader(io.BytesIO(overlay_bytes)).pages[0])
        writer.append_pages_from_reader(reader)
    else:
        writer.append_pages_from_reader(reader)
        signature_page_bytes, signature_page_fits = _signature_overlay_pdf(
            page_width=page_width,
            page_height=page_height,
            document=document,
            signatures=signatures,
            bottom=36,
        )
        if not signature_page_fits:
            raise ConclusionGenerationError(
                "Слишком много подписей для одной печатной формы заключения."
            )
        writer.add_page(PdfReader(io.BytesIO(signature_page_bytes)).pages[0])

    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def _xml_text(parent, tag, value):
    node = ElementTree.SubElement(
        parent,
        ElementTree.QName(SIGNATURE_XML_NAMESPACE, tag),
    )
    node.text = str(value or "")
    return node


def _build_signature_xml(
    document,
    signatures,
    *,
    source_pdf_name,
    source_pdf_bytes,
    printed_pdf_name,
    printed_pdf_bytes,
):
    ElementTree.register_namespace("edoc", SIGNATURE_XML_NAMESPACE)
    final_signed_at = timezone.localtime(signatures[-1].signed_at)
    root = ElementTree.Element(
        ElementTree.QName(SIGNATURE_XML_NAMESPACE, "wredcData"),
        {
            "id": str(document.package_id),
            "created": final_signed_at.date().isoformat(),
            "version": "1.0",
        },
    )
    content = ElementTree.SubElement(root, ElementTree.QName(SIGNATURE_XML_NAMESPACE, "content"))
    _xml_text(
        content,
        "employername",
        getattr(settings, "CONCLUSION_EMPLOYER_NAME", "ФГБОУ ВО «ТГТУ»"),
    )
    doc_info = ElementTree.SubElement(content, ElementTree.QName(SIGNATURE_XML_NAMESPACE, "docinfo"))
    _xml_text(doc_info, "docName", "Заключение о возможности открытого опубликования")
    _xml_text(doc_info, "date", final_signed_at.date().isoformat())
    _xml_text(doc_info, "docType", "conclusion")
    _xml_text(doc_info, "registrationNumber", document.registration_number)
    _xml_text(doc_info, "publicationName", document.submission.title)
    _xml_text(doc_info, "file", source_pdf_name)
    _xml_text(doc_info, "size", len(source_pdf_bytes))
    _xml_text(doc_info, "sha256", _sha256_bytes(source_pdf_bytes))
    attachment = ElementTree.SubElement(
        doc_info,
        ElementTree.QName(SIGNATURE_XML_NAMESPACE, "attachment"),
        {"extension": "pdf"},
    )
    _xml_text(attachment, "file", printed_pdf_name)
    _xml_text(attachment, "size", len(printed_pdf_bytes))
    _xml_text(attachment, "sha256", _sha256_bytes(printed_pdf_bytes))

    signatures_node = ElementTree.SubElement(
        doc_info,
        ElementTree.QName(SIGNATURE_XML_NAMESPACE, "signatures"),
    )
    for index, signature in enumerate(signatures):
        signed_at = timezone.localtime(signature.signed_at)
        signature_node = ElementTree.SubElement(
            signatures_node,
            ElementTree.QName(SIGNATURE_XML_NAMESPACE, "signature"),
        )
        _xml_text(signature_node, "operationId", signature.operation_id)
        _xml_text(signature_node, "signerName", signature.signer_name)
        _xml_text(signature_node, "signerRole", signature.signer_role)
        _xml_text(signature_node, "certificateType", "ПЭП ТГТУ")
        _xml_text(signature_node, "decision", _signature_status(index, len(signatures)))
        _xml_text(signature_node, "signedAt", signed_at.isoformat())
        _xml_text(signature_node, "timeZone", _format_timezone_offset(signed_at))
        _xml_text(signature_node, "simple", "true")
        _xml_text(signature_node, "eventHash", signature.event_hash)
        _xml_text(signature_node, "previousEventHash", signature.previous_event_hash)
        _xml_text(signature_node, "documentSha256", signature.document_sha256)
        _xml_text(signature_node, "submissionVersion", signature.submission_version_number)
        _xml_text(signature_node, "submissionVersionSha256", signature.submission_version_sha256)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


@transaction.atomic
def finalize_conclusion_package(document):
    document = ConclusionDocument.objects.select_for_update().get(pk=document.pk)
    if document.package_finalized_at:
        return document

    signatures = list(document.signatures.order_by("signed_at", "id"))
    if not signatures:
        raise ConclusionGenerationError("Нельзя сформировать комплект заключения без подписей.")

    source_pdf_bytes = _convert_conclusion_docx_to_pdf(document)
    printed_pdf_bytes = _build_printed_pdf(source_pdf_bytes, document, signatures)
    source_pdf_hash = _sha256_bytes(source_pdf_bytes)
    printed_pdf_hash = _sha256_bytes(printed_pdf_bytes)
    saved_files = []
    try:
        document.source_pdf_file.save(
            f"{document.package_id}.pdf",
            ContentFile(source_pdf_bytes),
            save=False,
        )
        saved_files.append((document.source_pdf_file.storage, document.source_pdf_file.name))
        document.printed_pdf_file.save(
            "Печатная_форма.pdf",
            ContentFile(printed_pdf_bytes),
            save=False,
        )
        saved_files.append((document.printed_pdf_file.storage, document.printed_pdf_file.name))
        signature_xml_bytes = _build_signature_xml(
            document,
            signatures,
            source_pdf_name=Path(document.source_pdf_file.name).name,
            source_pdf_bytes=source_pdf_bytes,
            printed_pdf_name=Path(document.printed_pdf_file.name).name,
            printed_pdf_bytes=printed_pdf_bytes,
        )
        document.signature_data_file.save(
            "wredc_data.xml",
            ContentFile(signature_xml_bytes),
            save=False,
        )
        saved_files.append((document.signature_data_file.storage, document.signature_data_file.name))
        document.source_pdf_sha256 = source_pdf_hash
        document.printed_pdf_sha256 = printed_pdf_hash
        document.signature_data_sha256 = _sha256_bytes(signature_xml_bytes)
        document.package_finalized_at = timezone.now()
        document.save(
            update_fields=[
                "source_pdf_file",
                "source_pdf_sha256",
                "printed_pdf_file",
                "printed_pdf_sha256",
                "signature_data_file",
                "signature_data_sha256",
                "package_finalized_at",
            ]
        )
    except Exception:
        for storage, file_name in saved_files:
            storage.delete(file_name)
        raise
    return document
