import json
import re
import uuid
from io import BytesIO
from pathlib import Path

from django.conf import settings
from docx import Document


TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
CITATION_NUMBER_RE = re.compile(r"\[(\d+)\]")


def _workspace_dir(user_id, token):
    if not TOKEN_RE.fullmatch(str(token or "")):
        raise ValueError("Некорректный идентификатор рабочего набора.")
    return Path(settings.CITATION_WORKSPACE_ROOT) / str(int(user_id)) / token


def create_workspace(*, user_id, file_bytes, file_name, snapshot, claims, index_status):
    token = uuid.uuid4().hex
    directory = _workspace_dir(user_id, token)
    directory.mkdir(parents=True, exist_ok=False)
    suffix = Path(file_name or "").suffix.casefold()
    source_name = f"source{suffix}" if file_bytes is not None and suffix else ""
    if source_name:
        (directory / source_name).write_bytes(file_bytes)
    payload = {
        "token": token,
        "file_name": Path(file_name or "document").name,
        "suffix": suffix,
        "source_name": source_name,
        "claims": claims,
        "index_status": index_status,
        "text_length": len(snapshot.get("text") or ""),
    }
    (directory / "workspace.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def load_workspace(*, user_id, token):
    directory = _workspace_dir(user_id, token)
    payload_path = directory / "workspace.json"
    if not payload_path.exists():
        raise FileNotFoundError("Рабочий набор не найден или уже удалён.")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["_directory"] = directory
    return payload


def _selected_sources(payload, selections):
    claims = {claim["id"]: claim for claim in payload.get("claims") or []}
    selected = []
    article_numbers = {}
    for item in selections:
        claim = claims.get(str(item.get("claim_id") or ""))
        if claim is None:
            continue
        article_id = str(item.get("article_id") or "")
        result = next(
            (
                candidate
                for candidate in claim.get("recommendations") or []
                if str(candidate.get("article_id")) == article_id
            ),
            None,
        )
        if result is None:
            continue
        if article_id not in article_numbers:
            article_numbers[article_id] = len(article_numbers)
        selected.append((claim, result, article_id))
    return selected, article_numbers


def _insert_marker_after_claim(paragraph, claim_text, marker):
    if marker in paragraph.text:
        return True
    full_text = "".join(run.text for run in paragraph.runs)
    normalized_chars = []
    source_positions = []
    previous_was_space = True
    for source_index, character in enumerate(full_text):
        if character.isspace():
            if not previous_was_space:
                normalized_chars.append(" ")
                source_positions.append(source_index)
            previous_was_space = True
            continue
        folded = character.casefold().replace("ё", "е")
        for folded_character in folded:
            normalized_chars.append(folded_character)
            source_positions.append(source_index)
        previous_was_space = False
    if normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        source_positions.pop()
    normalized_text = "".join(normalized_chars)
    normalized_claim = " ".join(claim_text.casefold().replace("ё", "е").split())
    position = normalized_text.find(normalized_claim)
    if position < 0 or not normalized_claim:
        return False
    normalized_end = position + len(normalized_claim) - 1
    insertion_at = source_positions[normalized_end] + 1
    offset = 0
    for run in paragraph.runs:
        run_end = offset + len(run.text)
        if insertion_at <= run_end:
            local_offset = max(0, insertion_at - offset)
            run.text = (
                run.text[:local_offset]
                + f" {marker}"
                + run.text[local_offset:]
            )
            return True
        offset = run_end
    return False


def apply_to_docx(*, user_id, token, selections):
    payload = load_workspace(user_id=user_id, token=token)
    if payload.get("suffix") != ".docx" or not payload.get("source_name"):
        raise ValueError("Автоматическая вставка доступна только для исходного DOCX.")
    selected, article_numbers = _selected_sources(payload, selections)
    if not selected:
        raise ValueError("Не выбрано ни одного источника.")

    source_path = payload["_directory"] / payload["source_name"]
    document = Document(source_path)
    existing_numbers = [
        int(value)
        for paragraph in document.paragraphs
        for value in CITATION_NUMBER_RE.findall(paragraph.text)
    ]
    start_number = max(existing_numbers, default=0) + 1
    number_by_article = {
        article_id: start_number + offset
        for article_id, offset in article_numbers.items()
    }

    for claim, _result, article_id in selected:
        marker = f"[{number_by_article[article_id]}]"
        target_text = " ".join(str(claim.get("text") or "").split())
        for paragraph in document.paragraphs:
            normalized = " ".join(paragraph.text.split())
            if target_text and target_text in normalized:
                if not _insert_marker_after_claim(paragraph, target_text, marker):
                    paragraph.add_run(f" {marker}")
                break

    bibliography_heading = next(
        (
            paragraph
            for paragraph in document.paragraphs
            if paragraph.text.strip().casefold()
            in {"список литературы", "библиографический список", "references"}
        ),
        None,
    )
    if bibliography_heading is None:
        document.add_heading("Список литературы", level=1)

    unique_results = {}
    for _claim, result, article_id in selected:
        unique_results.setdefault(article_id, result)
    for article_id, result in sorted(
        unique_results.items(),
        key=lambda item: number_by_article[item[0]],
    ):
        number = number_by_article[article_id]
        citation = str(result.get("citation") or result.get("title") or "").strip()
        document.add_paragraph(f"[{number}] {citation}")

    output = BytesIO()
    document.save(output)
    output.seek(0)
    original_stem = Path(payload.get("file_name") or "article").stem
    return output, f"{original_stem}_with_citations.docx"
