from pathlib import Path

from django import forms


class TemplateV2JobForm(forms.Form):
    article = forms.FileField(label="ARTICLE.docx / ARTICLE.doc", help_text="Исходная статья Word. DOCX или DOC до 120 МБ.")
    template = forms.FileField(label="TEMPLATE.docx / .doc / .dotx", help_text="Оформленный Word-образец или шаблон DOTX до 120 МБ.")

    def clean(self):
        data = super().clean()
        for field in ("article", "template"):
            upload = data.get(field)
            if upload is None:
                continue
            allowed = {".docx", ".doc", ".dotx"} if field == 'template' else {".docx", ".doc"}
            if Path(upload.name).suffix.lower() not in allowed:
                self.add_error(field, "Для V2 выберите Word-файл DOCX/DOC; для шаблона также допустим DOTX.")
            elif upload.size > 120 * 1024 * 1024:
                self.add_error(field, "Размер файла превышает 120 МБ.")
        return data
