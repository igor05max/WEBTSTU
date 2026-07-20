from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0005_activity_source_is_overridden"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ScientificResult",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_key", models.CharField(db_index=True, max_length=64, unique=True, verbose_name="Ключ импорта")),
                ("source_id", models.CharField(db_index=True, max_length=64, verbose_name="ID результата в источнике")),
                ("external_author_id", models.CharField(blank=True, db_index=True, max_length=32, verbose_name="Кадровый ID автора")),
                ("title", models.CharField(max_length=700, verbose_name="Фактический результат")),
                ("result_year", models.PositiveSmallIntegerField(db_index=True, verbose_name="Год результата")),
                ("academic_year", models.CharField(db_index=True, max_length=9, verbose_name="Учебный год")),
                ("publication_name", models.CharField(blank=True, max_length=700, verbose_name="Издание или мероприятие")),
                ("publication_details", models.CharField(blank=True, max_length=700, verbose_name="Выходные сведения")),
                ("bibliographic_data", models.TextField(blank=True, verbose_name="Библиографическое описание")),
                ("source_file", models.CharField(max_length=500, verbose_name="Файл-источник")),
                ("source_line", models.PositiveIntegerField(default=0, verbose_name="Строка источника")),
                ("source_payload", models.JSONField(blank=True, default=dict, verbose_name="Исходные данные")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создан")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлён")),
                ("activity_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="scientific_results", to="activities.activitytype", verbose_name="Тип результата")),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="scientific_results", to=settings.AUTH_USER_MODEL, verbose_name="Сотрудник")),
                ("planned_activity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="scientific_results", to="activities.activity", verbose_name="Пункт плана")),
            ],
            options={
                "verbose_name": "Фактический научный результат",
                "verbose_name_plural": "Фактические научные результаты",
                "ordering": ("-result_year", "activity_type__name", "title", "id"),
            },
        ),
    ]
