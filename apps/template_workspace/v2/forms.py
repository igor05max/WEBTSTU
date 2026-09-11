from pathlib import Path

from django import forms


class TemplateV2JobForm(forms.Form):
    article = forms.FileField(label="ARTICLE.docx / ARTICLE.doc", help_text="Исходная статья Word. DOCX или DOC до 120 МБ.")
    template = forms.FileField(label="TEMPLATE.docx / TEMPLATE.doc", help_text="Оформленный Word-образец. DOCX или DOC до 120 МБ.")

    def clean(self):
        data = super().clean()
        for field in ("article", "template"):
            upload = data.get(field)
            if upload is None:
                continue
            if Path(upload.name).suffix.lower() not in {".docx", ".doc"}:
                self.add_error(field, "Для V2 выберите Word-файл DOCX или DOC.")
            elif upload.size > 120 * 1024 * 1024:
                self.add_error(field, "Размер файла превышает 120 МБ.")
        return data
