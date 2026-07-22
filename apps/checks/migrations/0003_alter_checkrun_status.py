from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("checks", "0002_geminiconfiguration"),
    ]

    operations = [
        migrations.AlterField(
            model_name="checkrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Ожидает"),
                    ("running", "Выполняется"),
                    ("passed", "Пройдена"),
                    ("failed", "Не пройдена"),
                    ("partial", "Выполнена частично"),
                    ("not_performed", "Не выполнена"),
                ],
                default="pending",
                max_length=16,
                verbose_name="Статус",
            ),
        ),
    ]
