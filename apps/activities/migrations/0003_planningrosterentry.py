import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("activities", "0002_seed_activity_catalog"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlanningRosterEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("academic_year", models.CharField(db_index=True, max_length=9, verbose_name="Учебный год")),
                ("department_code", models.CharField(db_index=True, max_length=64, verbose_name="Кафедра")),
                ("full_name", models.CharField(max_length=255, verbose_name="ФИО из плана")),
                ("source_files", models.JSONField(blank=True, default=list, verbose_name="Файлы-источники")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="planning_roster_entries", to=settings.AUTH_USER_MODEL, verbose_name="Сотрудник")),
            ],
            options={
                "verbose_name": "Сотрудник из индивидуального плана",
                "verbose_name_plural": "Состав преподавателей из индивидуальных планов",
                "ordering": ("academic_year", "department_code", "full_name"),
            },
        ),
        migrations.AddConstraint(
            model_name="planningrosterentry",
            constraint=models.UniqueConstraint(fields=("academic_year", "department_code", "user"), name="unique_planning_roster_member"),
        ),
    ]
