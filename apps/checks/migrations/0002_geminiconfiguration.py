from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("checks", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="GeminiConfiguration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("model_name", models.CharField(blank=True, max_length=160, verbose_name="Модель")),
                ("available_models", models.JSONField(blank=True, default=list, verbose_name="Доступные модели")),
                ("models_refreshed_at", models.DateTimeField(blank=True, null=True, verbose_name="Список обновлён")),
                ("last_test_status", models.CharField(blank=True, max_length=32, verbose_name="Статус последней проверки")),
                ("last_test_details", models.JSONField(blank=True, default=dict, verbose_name="Диагностика")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Настройка Gemini",
                "verbose_name_plural": "Настройки Gemini",
            },
        ),
    ]
