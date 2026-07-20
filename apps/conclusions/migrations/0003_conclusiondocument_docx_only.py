import apps.conclusions.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conclusions", "0002_create_prorector_role"),
    ]

    operations = [
        migrations.RenameField(
            model_name="conclusiondocument",
            old_name="source_docx_file",
            new_name="document_file",
        ),
        migrations.RemoveField(
            model_name="conclusiondocument",
            name="pdf_file",
        ),
        migrations.AlterField(
            model_name="conclusiondocument",
            name="document_file",
            field=models.FileField(
                upload_to=apps.conclusions.models.conclusion_docx_upload_to,
                verbose_name="Подписываемое заключение DOCX",
            ),
        ),
        migrations.AlterField(
            model_name="conclusiondocument",
            name="document_sha256",
            field=models.CharField(
                db_index=True,
                max_length=64,
                verbose_name="SHA-256 подписываемого заключения",
            ),
        ),
        migrations.AlterField(
            model_name="conclusionsignature",
            name="document_sha256",
            field=models.CharField(
                max_length=64,
                verbose_name="SHA-256 подписанного заключения",
            ),
        ),
    ]
