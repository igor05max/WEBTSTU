from pathlib import Path

from django import forms


class TemplateJobForm(forms.Form):
    article = forms.FileField(label="Исходная статья", help_text="DOCX, PDF, TEX или ZIP с LaTeX-проектом. До 50 МБ.")
    template = forms.FileField(label="Шаблон оформления", help_text="DOCX, PDF, TEX или ZIP с файлами шаблона. До 50 МБ.")

    def clean(self):
        data = super().clean()
        for field in ("article", "template"):
            upload = data.get(field)
            if upload is None:
                continue
            if Path(upload.name).suffix.lower() not in {".docx", ".pdf", ".tex", ".zip"}:
                self.add_error(field, "Выберите DOCX, PDF, TEX или ZIP.")
            elif upload.size > 50 * 1024 * 1024:
                self.add_error(field, "Размер файла превышает 50 МБ.")
        return data

