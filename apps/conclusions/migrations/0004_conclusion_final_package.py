import apps.conclusions.models
from django.db import migrations, models
import uuid


def populate_package_ids(apps, schema_editor):
    ConclusionDocument = apps.get_model("conclusions", "ConclusionDocument")
    for document in ConclusionDocument.objects.filter(package_id__isnull=True).iterator():
        document.package_id = uuid.uuid4()
        document.save(update_fields=["package_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("conclusions", "0003_conclusiondocument_docx_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="conclusiondocument",
            name="package_id",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_package_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="conclusiondocument",
            name="package_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name="conclusiondocument",
            name="source_pdf_file",
            field=models.FileField(
                blank=True,
                upload_to=apps.conclusions.models.conclusion_package_upload_to,
                verbose_name="Исходное заключение PDF",
            ),
        ),
        migrations.AddField(
            model_name="conclusiondocument",
            name="source_pdf_sha256",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="conclusiondocument",
            name="printed_pdf_file",
            field=models.FileField(
                blank=True,
                upload_to=apps.conclusions.models.conclusion_package_upload_to,
                verbose_name="Печатная форма с подписями PDF",
            ),
        ),
        migrations.AddField(
            model_name="conclusiondocument",
            name="printed_pdf_sha256",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="conclusiondocument",
            name="signature_data_file",
            field=models.FileField(
                blank=True,
                upload_to=apps.conclusions.models.conclusion_package_upload_to,
                verbose_name="Данные электронных подписей XML",
            ),
        ),
        migrations.AddField(
            model_name="conclusiondocument",
            name="signature_data_sha256",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="conclusiondocument",
            name="package_finalized_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Комплект файлов сформирован",
            ),
        ),
    ]
