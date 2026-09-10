from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("template_workspace", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="templatejob",
            name="kind",
            field=models.CharField(
                choices=[("v1", "Legacy template workspace"), ("v2", "Word-first forensics")],
                default="v1",
                max_length=10,
            ),
        ),
    ]
