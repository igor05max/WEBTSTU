from django.db import migrations, models
import apps.template_workspace.models


class Migration(migrations.Migration):
    dependencies = [("template_workspace", "0002_templatejob_kind")]
    operations = [
        migrations.AlterField(
            model_name="templatejob", name="kind",
            field=models.CharField(max_length=10, default="v1", choices=[
                ("v1", "Legacy template workspace"), ("v2", "Word-first forensics"), ("jamt", "Стиль JAMT")]),
        ),
        migrations.AlterField(
            model_name="templatejob", name="template",
            field=models.FileField(blank=True, max_length=500, upload_to=apps.template_workspace.models.job_upload),
        ),
        migrations.AlterField(
            model_name="templatejob", name="template_name",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
