from django import forms

from apps.accounts.models import User
from apps.activities.models import Activity, ActivityType, GrantType


class ActivityForm(forms.ModelForm):
    class Meta:
        model = Activity
        fields = (
            "activity_type",
            "grant_type",
            "title",
            "quantity",
            "academic_year",
            "period",
            "status",
            "collaborators",
        )
        widgets = {
            "title": forms.Textarea(attrs={"rows": 3}),
            "collaborators": forms.SelectMultiple(attrs={"size": 6}),
        }
        help_texts = {
            "title": "Например: статья в журнале, заявка РНФ, учебное пособие или доклад на конференции.",
            "quantity": "Если количество не указано, оставьте 1.",
            "grant_type": "Заполняется только для результата «Грант». ",
            "collaborators": "Необязательно. Выберите сотрудников, которые участвуют вместе с вами.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["activity_type"].queryset = ActivityType.objects.filter(is_active=True)
        self.fields["grant_type"].queryset = GrantType.objects.filter(is_active=True)
        self.fields["collaborators"].queryset = User.objects.filter(is_active=True).order_by(
            "last_name", "first_name", "username"
        )
        self.fields["grant_type"].required = False
        self.fields["quantity"].required = False
        self.fields["collaborators"].required = False

    def clean_quantity(self):
        return self.cleaned_data.get("quantity") or 1

    def clean_academic_year(self):
        value = (self.cleaned_data.get("academic_year") or "").strip()
        if len(value) != 9 or value[4] != "/" or not value.replace("/", "").isdigit():
            raise forms.ValidationError("Укажите учебный год в формате 2025/2026.")
        start_year, end_year = (int(part) for part in value.split("/"))
        if end_year != start_year + 1:
            raise forms.ValidationError("Учебный год должен состоять из двух последовательных лет.")
        return value

    def clean(self):
        cleaned_data = super().clean()
        activity_type = cleaned_data.get("activity_type")
        grant_type = cleaned_data.get("grant_type")
        if activity_type and activity_type.requires_grant_type and not grant_type:
            self.add_error("grant_type", "Для гранта выберите его вид.")
        if activity_type and not activity_type.requires_grant_type and grant_type:
            self.add_error("grant_type", "Вид гранта можно указать только для гранта.")
        return cleaned_data
