import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.activities.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ActivityType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True, verbose_name="Код")),
                ("name", models.CharField(max_length=255, unique=True, verbose_name="Название")),
                (
                    "area",
                    models.CharField(
                        choices=[
                            ("research", "Научная работа"),
                            ("methodical", "Методическая работа"),
                            ("organisational", "Организационная и воспитательная работа"),
                            ("development", "Повышение квалификации"),
                            ("other", "Другое"),
                        ],
                        default="research",
                        max_length=32,
                        verbose_name="Направление",
                    ),
                ),
                ("requires_grant_type", models.BooleanField(default=False, verbose_name="Требует вида гранта")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
            ],
            options={
                "verbose_name": "Тип результата",
                "verbose_name_plural": "Типы результатов",
                "ordering": ("area", "name"),
            },
        ),
        migrations.CreateModel(
            name="GrantType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=64, unique=True, verbose_name="Код")),
                ("name", models.CharField(max_length=255, unique=True, verbose_name="Название")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
            ],
            options={
                "verbose_name": "Вид гранта",
                "verbose_name_plural": "Виды грантов",
                "ordering": ("name",),
            },
        ),
        migrations.CreateModel(
            name="Activity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=700, verbose_name="Планируемый результат")),
                (
                    "academic_year",
                    models.CharField(
                        db_index=True,
                        default=apps.activities.models.get_current_academic_year,
                        max_length=9,
                        verbose_name="Учебный год",
                    ),
                ),
                (
                    "period",
                    models.CharField(
                        choices=[
                            ("first_half", "I полугодие"),
                            ("second_half", "II полугодие"),
                            ("whole_year", "Весь учебный год"),
                        ],
                        default="whole_year",
                        max_length=16,
                        verbose_name="Период выполнения",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("planned", "Запланировано"),
                            ("in_progress", "В работе"),
                            ("completed", "Выполнено"),
                        ],
                        db_index=True,
                        default="planned",
                        max_length=16,
                        verbose_name="Статус",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                (
                    "activity_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activities",
                        to="activities.activitytype",
                        verbose_name="Тип результата",
                    ),
                ),
                (
                    "collaborators",
                    models.ManyToManyField(blank=True, related_name="joint_activities", to=settings.AUTH_USER_MODEL, verbose_name="Соисполнители"),
                ),
                (
                    "grant_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="activities",
                        to="activities.granttype",
                        verbose_name="Вид гранта",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="planned_activities",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Ответственный",
                    ),
                ),
            ],
            options={
                "verbose_name": "Планируемый результат",
                "verbose_name_plural": "Планируемые результаты",
                "ordering": ("academic_year", "period", "-updated_at", "-id"),
            },
        ),
    ]
