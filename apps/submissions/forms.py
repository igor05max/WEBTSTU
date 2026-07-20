from pathlib import Path

from django import forms
from django.conf import settings
from django.urls import reverse

from apps.accounts.models import User
from apps.directory.journal_search import resolve_journal_query
from apps.directory.models import Direction, Journal
from apps.submissions.models import Submission
from apps.submissions.document_analysis import SUPPORTED_EXTENSIONS
from apps.submissions.route_suggestions import (
    get_selectable_directions_queryset,
    get_selectable_route_templates_queryset,
)
from apps.workflow.models import RouteTemplate


class DirectionAwareRouteTemplateSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-direction-id"] = str(instance.direction_id or "")
            option["attrs"]["data-article-type-id"] = str(instance.article_type_id or "")
        return option


class UserChoiceSelectMultiple(forms.SelectMultiple):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-username"] = instance.username
            option["attrs"]["data-unit"] = getattr(instance.org_unit, "name", "")
        return option


class RouteTemplateChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        if obj.direction_id is None:
            return obj.name
        return f"{obj.direction.name} -> {obj.name}"


class SubmissionCreateForm(forms.ModelForm):
    journal = forms.ModelChoiceField(
        queryset=Journal.objects.none(),
        required=False,
        widget=forms.HiddenInput(),
    )
    journal_query = forms.CharField(
        label="Журнал",
        help_text="Введите название журнала или ISSN из белого списка.",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Например: 2053-1583 или 2D MATERIALS",
            }
        ),
    )
    file = forms.FileField(label="Файл материала")
    co_authors = forms.ModelMultipleChoiceField(
        label="Соавторы",
        queryset=User.objects.none(),
        required=False,
        help_text="Выберите остальных авторов материала. Вы как отправитель будете добавлены автоматически.",
        widget=UserChoiceSelectMultiple(attrs={"size": 8}),
    )
    version_comment = forms.CharField(
        label="Комментарий к версии",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Дополнительная информация для экспертов и куратора",
            }
        ),
    )

    def __init__(self, *args, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.select_related("org_unit").order_by("last_name", "first_name", "username")
        if current_user is not None and getattr(current_user, "id", None):
            queryset = queryset.exclude(pk=current_user.id)
        self.fields["co_authors"].queryset = queryset
        self.fields["article_type"].empty_label = "Выберите тип материала"
        self.fields["journal"].queryset = Journal.objects.filter(is_active=True)
        self.fields["journal_query"].widget.attrs["data-journal-search-url"] = reverse("directory:journal_search")
        self.fields["file"].widget.attrs["data-metadata-extract-url"] = reverse(
            "submissions:extract_metadata"
        )

    def clean_file(self):
        uploaded_file = self.cleaned_data.get("file")
        if uploaded_file is None:
            return uploaded_file
        suffix = Path(uploaded_file.name).suffix.casefold()
        if suffix not in SUPPORTED_EXTENSIONS:
            allowed = ", ".join(sorted(value.lstrip(".").upper() for value in SUPPORTED_EXTENSIONS))
            raise forms.ValidationError(f"Формат не поддерживается. Разрешены: {allowed}.")
        maximum_size = int(getattr(settings, "SUBMISSION_FILE_MAX_SIZE", 50 * 1024 * 1024))
        if uploaded_file.size > maximum_size:
            raise forms.ValidationError(
                f"Размер файла превышает {round(maximum_size / 1024 / 1024)} МБ."
            )
        return uploaded_file

    def clean(self):
        cleaned_data = super().clean()
        journal = cleaned_data.get("journal")
        journal_query = (cleaned_data.get("journal_query") or "").strip()

        if journal is not None:
            cleaned_data["journal_query"] = journal.name
            return cleaned_data

        journal = resolve_journal_query(journal_query)
        if journal is None:
            self.add_error(
                "journal_query",
                "Журнал не найден. Введите точный ISSN или выберите журнал из подсказок.",
            )
            return cleaned_data

        cleaned_data["journal"] = journal
        cleaned_data["journal_query"] = journal.name
        return cleaned_data

    class Meta:
        model = Submission
        fields = (
            "title",
            "abstract",
            "document_authors",
            "organizations",
            "contact_emails",
            "keywords",
            "journal_query",
            "journal",
            "article_type",
        )
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Введите название материала"}),
            "abstract": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Кратко опишите содержание и цели работы",
                }
            ),
            "document_authors": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "По одному автору на строке",
                }
            ),
            "organizations": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "Организации и кафедры авторов",
                }
            ),
            "contact_emails": forms.TextInput(
                attrs={"placeholder": "author@example.ru"}
            ),
            "keywords": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "Ключевые слова через запятую",
                }
            ),
        }


class SubmissionVersionUploadForm(forms.Form):
    file = forms.FileField(label="Новая версия файла")
    comment = forms.CharField(
        label="Комментарий к версии",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class SubmissionSubmitForm(forms.Form):
    direction = forms.ModelChoiceField(
        label="Область экспертизы",
        queryset=Direction.objects.none(),
        required=False,
        empty_label="Выберите область",
        help_text="От выбранной области зависит, какому тематическому эксперту сначала уйдет материал.",
        widget=forms.Select(attrs={"data-field-role": "direction-select"}),
    )
    route_template = RouteTemplateChoiceField(
        label="Маршрут согласования",
        queryset=RouteTemplate.objects.none(),
        required=False,
        empty_label="Выберите маршрут",
        help_text="Если у области только один маршрут, он подставится автоматически.",
        widget=DirectionAwareRouteTemplateSelect(attrs={"data-field-role": "route-template-select"}),
    )

    def __init__(self, *args, current_direction=None, current_route_template=None, current_article_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_article_type = current_article_type
        self.fields["direction"].queryset = get_selectable_directions_queryset(article_type=current_article_type)
        self.fields["route_template"].queryset = get_selectable_route_templates_queryset(article_type=current_article_type)
        self.single_selectable_direction = None
        self.single_selectable_route_template = None
        self.route_templates_by_direction = {}

        for route_template in self.fields["route_template"].queryset:
            direction_key = str(route_template.direction_id) if route_template.direction_id is not None else None
            self.route_templates_by_direction.setdefault(direction_key, []).append(route_template)

        direction_options = list(self.fields["direction"].queryset)
        route_template_options = list(self.fields["route_template"].queryset)
        if len(direction_options) == 1:
            self.single_selectable_direction = direction_options[0]
        if len(route_template_options) == 1:
            self.single_selectable_route_template = route_template_options[0]

        direction_value = self.data.get("direction") or getattr(current_direction, "pk", current_direction)
        if not direction_value and self.single_selectable_direction is not None:
            direction_value = self.single_selectable_direction.pk

        if not direction_value and self.single_selectable_route_template is None:
            self.fields["route_template"].widget.attrs["disabled"] = "disabled"

        if current_direction is not None:
            self.fields["direction"].initial = current_direction
        elif self.single_selectable_direction is not None:
            self.fields["direction"].initial = self.single_selectable_direction

        if current_route_template is None:
            single_route_template = self._get_single_route_template_for_direction(direction_value)
            if single_route_template is not None:
                self.fields["route_template"].initial = single_route_template
            elif self.single_selectable_route_template is not None:
                self.fields["route_template"].initial = self.single_selectable_route_template

        if current_route_template is not None:
            self.fields["route_template"].initial = current_route_template

    def _get_single_route_template_for_direction(self, direction):
        direction_id = getattr(direction, "pk", direction)

        direction_templates = list(self.route_templates_by_direction.get(None, []))
        if direction_id:
            direction_templates.extend(self.route_templates_by_direction.get(str(direction_id), []))

        unique_templates = []
        seen_ids = set()
        for route_template in direction_templates:
            if route_template.id in seen_ids:
                continue
            seen_ids.add(route_template.id)
            unique_templates.append(route_template)

        if len(unique_templates) != 1:
            return None
        return unique_templates[0]

    def clean(self):
        cleaned_data = super().clean()
        direction = cleaned_data.get("direction")
        route_template = cleaned_data.get("route_template")

        if direction is None and self.single_selectable_direction is not None:
            direction = self.single_selectable_direction
            cleaned_data["direction"] = direction

        if direction is None:
            self.add_error("direction", "Выберите область экспертизы.")
            return cleaned_data

        if route_template is None and direction is not None:
            route_template = self._get_single_route_template_for_direction(direction)
            if route_template is not None:
                cleaned_data["route_template"] = route_template
        if route_template is None and self.single_selectable_route_template is not None:
            route_template = self.single_selectable_route_template
            cleaned_data["route_template"] = route_template

        if route_template is None:
            self.add_error("route_template", "Выберите маршрут согласования.")
            return cleaned_data

        if route_template.direction_id is not None and route_template.direction_id != direction.id:
            self.add_error(
                "route_template",
                "Выбранный маршрут не относится к указанной области экспертизы.",
            )
        elif (
            self.current_article_type is not None
            and route_template.article_type_id not in (None, self.current_article_type.id)
        ):
            self.add_error(
                "route_template",
                "Выбранный маршрут не относится к указанному типу материала.",
            )

        return cleaned_data


class SubmissionAppealForm(forms.Form):
    comment = forms.CharField(
        label="Комментарий к апелляции",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Опишите, почему отклонение нужно пересмотреть.",
    )
    attachment = forms.FileField(
        label="Файл апелляции",
        required=False,
        help_text="Необязательно. Можно приложить новый файл или пояснение.",
    )

    def clean_comment(self):
        comment = (self.cleaned_data.get("comment") or "").strip()
        if not comment:
            raise forms.ValidationError("Комментарий к апелляции обязателен.")
        return comment


class SubmissionAppealDecisionForm(forms.Form):
    comment = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, require_comment=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_comment = require_comment
        if require_comment:
            self.fields["comment"].help_text = "Комментарий обязателен при отклонении апелляции."

    def clean_comment(self):
        comment = (self.cleaned_data.get("comment") or "").strip()
        if self.require_comment and not comment:
            raise forms.ValidationError("Комментарий обязателен для этого результата.")
        return comment
