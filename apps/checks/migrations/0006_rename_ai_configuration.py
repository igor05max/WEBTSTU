from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("checks", "0005_checkrun_claim_token_and_unique_run"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="GeminiConfiguration",
            new_name="AIConfiguration",
        ),
        migrations.AlterModelOptions(
            name="aiconfiguration",
            options={
                "verbose_name": "Настройка локальной AI-модели",
                "verbose_name_plural": "Настройки локальной AI-модели",
            },
        ),
    ]
