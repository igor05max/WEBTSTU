from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0006_journal_search_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="journal",
            name="editorial_policy",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "JSON с required_sections, min_words, max_words и "
                    "disallow_uncited_references. Пустое значение использует общие правила."
                ),
                verbose_name="Политика автоматической проверки",
            ),
        ),
        migrations.AddField(
            model_name="articletype",
            name="max_word_count",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="Максимум слов"),
        ),
        migrations.AddField(
            model_name="articletype",
            name="min_word_count",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="Минимум слов"),
        ),
    ]
