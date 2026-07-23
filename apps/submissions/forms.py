from pathlib import Path

from django import forms
from django.conf import settings
from django.urls import reverse

from apps.accounts.models import User
from apps.directory.formatting_templates import (
    TEMPLATE_EXTENSIONS,
    get_latest_formatting_template,
)
from apps.directory.journal_search import resolve_journal_query
from apps.directory.models import (
    Direction,
    FormattingTemplate,
    Journal,
    PublicationTopic,
)
from apps.submissions.models import Submission
from apps.submissions.document_analysis import SUPPORTED_EXTENSIONS
from apps.submissions.route_suggestions import (
    get_selectable_directions_queryset,
    get_selectable_route_templates_queryset,
)
from apps.workflow.models import RouteTemplate
from document_template_engine import BLOCK_CATALOG, normalize_template_rules


def _is_article_type(article_type):
    code = str(getattr(article_type, "code", "") or "").casefold()
    return code == "article" or code.endswith("-article") or code.startswith("article-")


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


class ArticleTypeSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-code"] = instance.code
            option["attrs"]["data-destination-kind"] = (
                "journal" if _is_article_type(instance) else "topic"
            )
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
        required=False,
        help_text="Введите название журнала или ISSN из белого списка.",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Например: 2053-1583 или 2D MATERIALS",
            }
        ),
    )
    publication_topic = forms.ModelChoiceField(
        queryset=PublicationTopic.objects.none(),
        required=False,
        widget=forms.HiddenInput(),
    )
    publication_topic_query = forms.CharField(
        label="Тема или событие",
        required=False,
        help_text="Начните вводить название конференции, темы или другого события.",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Например: Информационные технологии 2027",
            }
        ),
    )
    formatting_template = forms.ModelChoiceField(
        queryset=FormattingTemplate.objects.none(),
        required=False,
        widget=forms.HiddenInput(),
    )
    formatting_template_file = forms.FileField(
        label="Шаблон оформления",
        required=False,
        help_text="Можно загрузить DOCX, DOC, PDF, текстовый файл или изображение.",
    )
    formatting_check_requested = forms.BooleanField(
        label="Проверить оформление по шаблону",
        required=False,
        initial=True,
        help_text="Проверка необязательна и не блокирует отправку.",
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
        article_type_field = self.fields["article_type"]
        article_type_field.empty_label = "Выберите тип материала"
        article_type_widget = ArticleTypeSelect(
            attrs={
                **article_type_field.widget.attrs,
                "data-field-role": "article-type",
            }
        )
        article_type_widget.choices = article_type_field.choices
        article_type_field.widget = article_type_widget
        self.fields["journal"].queryset = Journal.objects.filter(is_active=True)
        self.fields["publication_topic"].queryset = PublicationTopic.objects.filter(
            is_active=True,
            merged_into__isnull=True,
        )
        self.fields["formatting_template"].queryset = FormattingTemplate.objects.all()
        self.fields["journal_query"].widget.attrs["data-journal-search-url"] = reverse("directory:journal_search")
        self.fields["publication_topic_query"].widget.attrs["data-topic-search-url"] = reverse(
            "directory:publication_topic_search"
        )
        self.fields["formatting_template"].widget.attrs["data-template-detail-url"] = reverse(
            "directory:formatting_template_detail",
            args=[0],
        ).replace("/0/", "/{id}/")
        self.fields["formatting_template_file"].widget.attrs["accept"] = ",".join(
            sorted(TEMPLATE_EXTENSIONS)
        )
        self.fields["file"].widget.attrs["data-metadata-extract-url"] = reverse(
            "submissions:extract_metadata"
        )
        self.fields["title"].required = False
        self.fields["abstract"].required = False

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

    def clean_formatting_template_file(self):
        uploaded_file = self.cleaned_data.get("formatting_template_file")
        if uploaded_file is None:
            return uploaded_file
        suffix = Path(uploaded_file.name).suffix.casefold()
        if suffix not in TEMPLATE_EXTENSIONS:
            allowed = ", ".join(sorted(value.lstrip(".").upper() for value in TEMPLATE_EXTENSIONS))
            raise forms.ValidationError(f"Формат шаблона не поддерживается. Разрешены: {allowed}.")
        maximum_size = int(getattr(settings, "SUBMISSION_FILE_MAX_SIZE", 50 * 1024 * 1024))
        if uploaded_file.size > maximum_size:
            raise forms.ValidationError(
                f"Размер шаблона превышает {round(maximum_size / 1024 / 1024)} МБ."
            )
        return uploaded_file

    def clean(self):
        cleaned_data = super().clean()
        article_type = cleaned_data.get("article_type")
        if article_type is None:
            return cleaned_data

        journal = cleaned_data.get("journal")
        journal_query = (cleaned_data.get("journal_query") or "").strip()
        publication_topic = cleaned_data.get("publication_topic")
        topic_query = (cleaned_data.get("publication_topic_query") or "").strip()
        selected_template = cleaned_data.get("formatting_template")
        uploaded_template = cleaned_data.get("formatting_template_file")

        if _is_article_type(article_type):
            cleaned_data["publication_topic"] = None
            cleaned_data["publication_topic_query"] = ""
            if journal is None:
                journal = resolve_journal_query(journal_query)
            if journal is None:
                self.add_error(
                    "journal_query",
                    "Журнал не найден. Введите точный ISSN или выберите журнал из подсказок.",
                )
                return cleaned_data
            cleaned_data["journal"] = journal
            cleaned_data["journal_query"] = journal.name
            latest_template = get_latest_formatting_template(
                article_type=article_type,
                journal=journal,
            )
            if selected_template is None:
                selected_template = latest_template
                cleaned_data["formatting_template"] = selected_template
            if uploaded_template is None and selected_template is None:
                self.add_error(
                    "formatting_template_file",
                    "Для этого журнала ещё нет шаблона. Загрузите его перед отправкой статьи.",
                )
                return cleaned_data
            if selected_template is not None and (
                selected_template.journal_id != journal.id
                or selected_template.article_type_id != article_type.id
            ):
                self.add_error("formatting_template", "Выбранный шаблон не относится к этому журналу.")
            return cleaned_data

        cleaned_data["journal"] = None
        cleaned_data["journal_query"] = ""
        if publication_topic is not None:
            topic_query = publication_topic.name
        if not topic_query:
            self.add_error("publication_topic_query", "Укажите тему или название события.")
            return cleaned_data
        cleaned_data["publication_topic_query"] = topic_query
        if publication_topic is not None:
            latest_template = get_latest_formatting_template(
                article_type=article_type,
                publication_topic=publication_topic,
            )
            if selected_template is None:
                selected_template = latest_template
                cleaned_data["formatting_template"] = selected_template
            if selected_template is not None and (
                selected_template.publication_topic_id != publication_topic.id
                or selected_template.article_type_id != article_type.id
            ):
                self.add_error(
                    "formatting_template",
                    "Выбранный шаблон не относится к этой теме или событию.",
                )
        elif selected_template is not None:
            self.add_error(
                "formatting_template",
                "Сначала выберите существующую тему или загрузите новый шаблон.",
            )
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
            "publication_topic_query",
            "publication_topic",
            "article_type",
            "formatting_template",
            "formatting_check_requested",
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


class FormattingRulesForm(forms.Form):
    _BLOCK_CHOICES = [
        (role, BLOCK_CATALOG[role]["label"])
        for role in (
            "udc",
            "title",
            "authors",
            "supervisor",
            "institution",
            "city_country",
            "abstract",
            "keywords",
            "references",
        )
    ]

    font_family = forms.CharField(label="Основной шрифт", required=False)
    font_size_pt = forms.DecimalField(
        label="Размер шрифта, пт",
        required=False,
        min_value=6,
        max_value=36,
        decimal_places=1,
    )
    line_spacing = forms.DecimalField(
        label="Межстрочный интервал",
        required=False,
        min_value=0.8,
        max_value=4,
        decimal_places=2,
    )
    first_line_indent_cm = forms.DecimalField(
        label="Абзацный отступ, см",
        required=False,
        min_value=0,
        max_value=5,
        decimal_places=2,
    )
    margin_top_cm = forms.DecimalField(label="Верхнее поле, см", required=False, min_value=0, max_value=10)
    margin_right_cm = forms.DecimalField(label="Правое поле, см", required=False, min_value=0, max_value=10)
    margin_bottom_cm = forms.DecimalField(label="Нижнее поле, см", required=False, min_value=0, max_value=10)
    margin_left_cm = forms.DecimalField(label="Левое поле, см", required=False, min_value=0, max_value=10)
    min_words = forms.IntegerField(label="Минимум слов", required=False, min_value=0)
    max_words = forms.IntegerField(label="Максимум слов", required=False, min_value=1)
    required_sections = forms.CharField(
        label="Обязательные разделы",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "По одному разделу на строке"}),
    )
    required_document_blocks = forms.MultipleChoiceField(
        label="Обязательные блоки документа",
        required=False,
        choices=_BLOCK_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    title_uppercase = forms.BooleanField(
        label="Название прописными буквами",
        required=False,
    )

    def clean(self):
        cleaned_data = super().clean()
        minimum = cleaned_data.get("min_words")
        maximum = cleaned_data.get("max_words")
        if minimum is not None and maximum is not None and minimum > maximum:
            self.add_error("max_words", "Максимум слов не может быть меньше минимума.")
        return cleaned_data

    @classmethod
    def from_snapshot(cls, snapshot, *args, **kwargs):
        effective = normalize_template_rules((snapshot or {}).get("effective") or {})
        body = effective.get("body") or {}
        page = effective.get("page") or {}
        margins = page.get("margins_cm") or {}
        limits = effective.get("limits") or {}
        structure = effective.get("structure") or {}
        blocks = (effective.get("document") or {}).get("blocks") or []
        title_block = next(
            (block for block in blocks if block.get("role") == "title"),
            {},
        )
        kwargs.setdefault(
            "initial",
            {
                "font_family": body.get("font_family") or "",
                "font_size_pt": body.get("font_size_pt"),
                "line_spacing": body.get("line_spacing"),
                "first_line_indent_cm": body.get("first_line_indent_cm"),
                "margin_top_cm": margins.get("top"),
                "margin_right_cm": margins.get("right"),
                "margin_bottom_cm": margins.get("bottom"),
                "margin_left_cm": margins.get("left"),
                "min_words": limits.get("min_words"),
                "max_words": limits.get("max_words"),
                "required_sections": "\n".join(structure.get("required_sections") or []),
                "required_document_blocks": [
                    block.get("role")
                    for block in blocks
                    if block.get("required") and block.get("role") != "body"
                ],
                "title_uppercase": bool(
                    (title_block.get("constraints") or {}).get("uppercase")
                ),
            },
        )
        return cls(*args, **kwargs)

    def apply_to_snapshot(self, snapshot):
        previous_effective = normalize_template_rules(
            (snapshot or {}).get("effective") or {}
        )
        updated = {
            **(snapshot or {}),
            "effective": {
                **previous_effective,
            },
        }
        effective = updated["effective"]
        effective["body"] = {
            **(effective.get("body") or {}),
            "font_family": self.cleaned_data.get("font_family") or "",
            "font_size_pt": float(self.cleaned_data["font_size_pt"]) if self.cleaned_data.get("font_size_pt") is not None else None,
            "line_spacing": float(self.cleaned_data["line_spacing"]) if self.cleaned_data.get("line_spacing") is not None else None,
            "first_line_indent_cm": float(self.cleaned_data["first_line_indent_cm"]) if self.cleaned_data.get("first_line_indent_cm") is not None else None,
        }
        effective["page"] = {
            **(effective.get("page") or {}),
            "margins_cm": {
                "top": float(self.cleaned_data["margin_top_cm"]) if self.cleaned_data.get("margin_top_cm") is not None else None,
                "right": float(self.cleaned_data["margin_right_cm"]) if self.cleaned_data.get("margin_right_cm") is not None else None,
                "bottom": float(self.cleaned_data["margin_bottom_cm"]) if self.cleaned_data.get("margin_bottom_cm") is not None else None,
                "left": float(self.cleaned_data["margin_left_cm"]) if self.cleaned_data.get("margin_left_cm") is not None else None,
            },
        }
        effective["limits"] = {
            **(effective.get("limits") or {}),
            "min_words": self.cleaned_data.get("min_words"),
            "max_words": self.cleaned_data.get("max_words"),
        }
        effective["structure"] = {
            **(effective.get("structure") or {}),
            "required_sections": [
                value.strip()
                for value in (self.cleaned_data.get("required_sections") or "").splitlines()
                if value.strip()
            ],
        }
        selected_blocks = set(self.cleaned_data.get("required_document_blocks") or [])
        existing_blocks = {
            block.get("role"): {**block}
            for block in ((effective.get("document") or {}).get("blocks") or [])
            if block.get("role")
        }
        for role, label in self._BLOCK_CHOICES:
            if role not in existing_blocks and role in selected_blocks:
                existing_blocks[role] = {
                    "role": role,
                    "label": label,
                    "required": True,
                }
            elif role in existing_blocks:
                existing_blocks[role]["required"] = role in selected_blocks
        title_block = existing_blocks.get("title")
        if title_block is not None:
            title_block["constraints"] = {
                **(title_block.get("constraints") or {}),
                "uppercase": bool(self.cleaned_data.get("title_uppercase")),
            }
        effective["document"] = {
            **(effective.get("document") or {}),
            "blocks": list(existing_blocks.values()),
        }
        effective = normalize_template_rules(effective)
        updated["effective"] = effective
        sources = [
            source
            for source in (updated.get("sources") or [])
            if source.get("kind") != "manual"
        ]
        sources.append({"kind": "manual", "label": "Уточнено автором для этой работы", "priority": 40})
        updated["sources"] = sources

        previous_values = {
            "body.font_family": (previous_effective.get("body") or {}).get("font_family"),
            "body.font_size_pt": (previous_effective.get("body") or {}).get("font_size_pt"),
            "body.line_spacing": (previous_effective.get("body") or {}).get("line_spacing"),
            "body.first_line_indent_cm": (previous_effective.get("body") or {}).get(
                "first_line_indent_cm"
            ),
            "page.margins_cm.top": (
                (previous_effective.get("page") or {}).get("margins_cm") or {}
            ).get("top"),
            "page.margins_cm.right": (
                (previous_effective.get("page") or {}).get("margins_cm") or {}
            ).get("right"),
            "page.margins_cm.bottom": (
                (previous_effective.get("page") or {}).get("margins_cm") or {}
            ).get("bottom"),
            "page.margins_cm.left": (
                (previous_effective.get("page") or {}).get("margins_cm") or {}
            ).get("left"),
            "limits.min_words": (previous_effective.get("limits") or {}).get("min_words"),
            "limits.max_words": (previous_effective.get("limits") or {}).get("max_words"),
            "structure.required_sections": (
                previous_effective.get("structure") or {}
            ).get("required_sections")
            or [],
            "document.required_blocks": sorted(
                block.get("role")
                for block in ((previous_effective.get("document") or {}).get("blocks") or [])
                if block.get("required")
            ),
        }
        selected_values = {
            "body.font_family": effective["body"]["font_family"],
            "body.font_size_pt": effective["body"]["font_size_pt"],
            "body.line_spacing": effective["body"]["line_spacing"],
            "body.first_line_indent_cm": effective["body"]["first_line_indent_cm"],
            "page.margins_cm.top": effective["page"]["margins_cm"]["top"],
            "page.margins_cm.right": effective["page"]["margins_cm"]["right"],
            "page.margins_cm.bottom": effective["page"]["margins_cm"]["bottom"],
            "page.margins_cm.left": effective["page"]["margins_cm"]["left"],
            "limits.min_words": effective["limits"]["min_words"],
            "limits.max_words": effective["limits"]["max_words"],
            "structure.required_sections": effective["structure"]["required_sections"],
            "document.required_blocks": sorted(
                block.get("role")
                for block in ((effective.get("document") or {}).get("blocks") or [])
                if block.get("required")
            ),
        }
        conflicts = [
            conflict
            for conflict in (updated.get("conflicts") or [])
            if conflict.get("source") != "manual_override"
        ]
        for field_name, selected_value in selected_values.items():
            previous_value = previous_values.get(field_name)
            if previous_value == selected_value:
                continue
            conflicts.append(
                {
                    "field": field_name,
                    "lower_value": previous_value,
                    "selected_value": selected_value,
                    "source": "manual_override",
                    "message": (
                        "Ручное уточнение автора имеет приоритет над ранее "
                        "извлечённым требованием."
                    ),
                }
            )
        updated["conflicts"] = conflicts
        return updated


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
