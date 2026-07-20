from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0003_submission_route_template"),
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
                    ("approved", "Согласована"),
                    ("rejected", "Отклонена"),
                ],
                default="draft",
                max_length=32,
                verbose_name="Статус",
            ),
        ),
    ]
