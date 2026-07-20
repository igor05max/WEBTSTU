from django import forms


class TaskDecisionForm(forms.Form):
    comment = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, require_comment=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_comment = require_comment
        if require_comment:
            self.fields["comment"].help_text = "Комментарий обязателен для отрицательного результата проверки."

    def clean_comment(self):
        comment = (self.cleaned_data.get("comment") or "").strip()
        if self.require_comment and not comment:
            raise forms.ValidationError("Комментарий обязателен для этого результата.")
        return comment
