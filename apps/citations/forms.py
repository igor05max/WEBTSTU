from pathlib import Path

from django import forms

from apps.submissions.models import Submission


SUPPORTED_UPLOAD_EXTENSIONS = {".docx", ".pdf", ".txt", ".md", ".rtf"}


class CitationSearchForm(forms.Form):
    submission = forms.ModelChoiceField(
        label="Материал из системы",
        queryset=Submission.objects.none(),
        required=False,
        empty_label="Выберите материал",
    )
    file = forms.FileField(
        label="Или загрузите статью",
        required=False,
        help_text="DOCX, PDF, TXT, MD или RTF, не более 50 МБ.",
    )
    text = forms.CharField(
        label="Или вставьте фрагмент текста",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "placeholder": "Введение, обзор литературы или отдельные утверждения…",
            }
        ),
    )
    max_claims = forms.IntegerField(
        label="Сколько фрагментов проверить",
        min_value=1,
        max_value=16,
        initial=6,
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["submission"].queryset = (
                Submission.objects.filter(author=user, current_version__isnull=False)
                .select_related("current_version")
                .order_by("-updated_at")
            )

    def clean_file(self):
        uploaded = self.cleaned_data.get("file")
        if uploaded is None:
            return None
        suffix = Path(uploaded.name).suffix.casefold()
        if suffix not in SUPPORTED_UPLOAD_EXTENSIONS:
            raise forms.ValidationError("Поддерживаются DOCX, PDF, TXT, MD и RTF.")
        if uploaded.size > 50 * 1024 * 1024:
            raise forms.ValidationError("Файл превышает ограничение 50 МБ.")
        return uploaded

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("file") is not None:
            cleaned["submission"] = None
            cleaned["text"] = ""
            return cleaned
        if cleaned.get("submission") is not None:
            cleaned["text"] = ""
            return cleaned
        if (cleaned.get("text") or "").strip():
            return cleaned
        raise forms.ValidationError(
            "Загрузите файл, выберите материал или вставьте текст."
        )
