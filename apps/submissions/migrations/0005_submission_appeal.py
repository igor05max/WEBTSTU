import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflow", "0006_alter_workflowstep_step_template"),
        ("submissions", "0004_alter_submission_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="submission",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Создана"),
                    ("submitted", "Готова к отправке"),
                    ("auto_checking", "Проверяется"),
                    ("in_review", "На согласовании"),
                    ("revision_requested", "Требует доработки"),
                    ("appeal_pending", "Апелляция на рассмотрении"),
                    ("approved", "Согласована"),
                    ("rejected", "Отклонена"),
                ],
                default="draft",
                max_length=32,
                verbose_name="Статус",
            ),
        ),
        migrations.CreateModel(
            name="SubmissionAppeal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("comment", models.TextField(verbose_name="Комментарий автора")),
                (
                    "attachment",
                    models.FileField(blank=True, upload_to="submission_appeals/", verbose_name="Файл апелляции"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "На рассмотрении"),
                            ("approved", "Апелляция принята"),
                            ("rejected", "Апелляция отклонена"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="Статус апелляции",
                    ),
                ),
                ("decision_comment", models.TextField(blank=True, verbose_name="Комментарий по апелляции")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создана")),
                ("decided_at", models.DateTimeField(blank=True, null=True, verbose_name="Решение принято")),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="submission_appeals",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Автор апелляции",
                    ),
                ),
                (
                    "decided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="decided_submission_appeals",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кто принял решение",
                    ),
                ),
                (
                    "rejected_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="submission_appeals",
                        to="workflow.approvaltask",
                        verbose_name="Отклоняющая задача",
                    ),
                ),
                (
                    "reviewer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="review_submission_appeals",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кто рассматривает апелляцию",
                    ),
                ),
                (
                    "submission",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="appeal",
                        to="submissions.submission",
                        verbose_name="Заявка",
                    ),
                ),
            ],
            options={
                "verbose_name": "Апелляция по заявке",
                "verbose_name_plural": "Апелляции по заявкам",
                "ordering": ("-created_at",),
            },
        ),
    ]
