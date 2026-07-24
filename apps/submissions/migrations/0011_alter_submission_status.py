from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0010_upgrade_template_rule_schema"),
    ]

    operations = [
        migrations.AlterField(
            model_name="submission",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Создана"),
                    ("submitted", "Готова к отправке"),
                    ("auto_checking", "Проверка идёт"),
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
    ]
