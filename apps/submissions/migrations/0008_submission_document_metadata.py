from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0007_submission_authors"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="contact_emails",
            field=models.TextField(blank=True, verbose_name="Контактные e-mail"),
        ),
        migrations.AddField(
            model_name="submission",
            name="document_authors",
            field=models.TextField(
                blank=True,
                help_text="Имена авторов в том виде, в котором они указаны в загруженном материале.",
                verbose_name="Авторы из документа",
            ),
        ),
        migrations.AddField(
            model_name="submission",
            name="keywords",
            field=models.TextField(blank=True, verbose_name="Ключевые слова"),
        ),
        migrations.AddField(
            model_name="submission",
            name="organizations",
            field=models.TextField(blank=True, verbose_name="Организации авторов"),
        ),
    ]
