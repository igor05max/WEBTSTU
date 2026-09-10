from pathlib import Path

from django import forms


class TemplateV2JobForm(forms.Form):
    article = forms.FileField(label="ARTICLE.docx", help_text="Исходная статья Word DOCX. До 50 МБ.")
    template = forms.FileField(label="TEMPLATE.docx", help_text="Оформленный Word-образец DOCX. До 50 МБ.")

    def clean(self):
        data = super().clean()
        for field in ("article", "template"):
            upload = data.get(field)
            if upload is None:
                continue
            if Path(upload.name).suffix.lower() != ".docx":
                self.add_error(field, "Для V2 выберите именно DOCX.")
            elif upload.size > 50 * 1024 * 1024:
                self.add_error(field, "Размер файла превышает 50 МБ.")
        return data
