import hashlib
import io
import json
from pathlib import Path
import re
import uuid
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.conclusions.models import ConclusionDocument, ConclusionSignature
from apps.workflow.models import AssigneeKind, WorkflowStep


PRORECTOR_ROLE_NAME = "Проректор по научной работе"
PRORECTOR_STEP_NAME = "Утверждение проректором по научной работе"


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
    return {
        "is_valid": current_hash == document.document_sha256,
        "expected_sha256": document.document_sha256,
        "actual_sha256": current_hash,
    }
