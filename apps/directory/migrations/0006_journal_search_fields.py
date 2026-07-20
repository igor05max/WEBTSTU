from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("directory", "0005_position_alter_articletype_options_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="journal",
            name="issn",
            field=models.CharField(blank=True, db_index=True, max_length=128, verbose_name="ISSN"),
        ),
        migrations.AddField(
            model_name="journal",
            name="search_index",
            field=models.TextField(blank=True, verbose_name="Поисковый индекс"),
        ),
        migrations.AddField(
            model_name="journal",
            name="white_list_level",
            field=models.PositiveSmallIntegerField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="Уровень белого списка",
            ),
        ),
    ]
