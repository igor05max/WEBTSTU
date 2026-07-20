from django.db import migrations, models
import django.db.models.deletion


def fill_missing_route_directions(apps, schema_editor):
    Direction = apps.get_model("directory", "Direction")
    RouteTemplate = apps.get_model("workflow", "RouteTemplate")

    default_direction, _ = Direction.objects.get_or_create(
        code="general",
        defaults={
            "name": "Общее направление",
            "description": "Автоматически создано для шаблонов маршрутов без направления.",
            "is_active": True,
        },
    )
    RouteTemplate.objects.filter(direction__isnull=True).update(direction=default_direction)


class Migration(migrations.Migration):
    dependencies = [
        ("workflow", "0003_seed_default_direction"),
    ]

    operations = [
        migrations.RunPython(fill_missing_route_directions, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="routetemplate",
            name="article_type",
        ),
        migrations.RemoveField(
            model_name="routetemplate",
            name="journal",
        ),
        migrations.AlterField(
            model_name="routetemplate",
            name="direction",
            field=models.ForeignKey(
                help_text="Пользователь выбирает направление при отправке статьи. По нему система подберет маршрут согласования.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="route_templates",
                to="directory.direction",
                verbose_name="Направление",
            ),
        ),
        migrations.AlterField(
            model_name="routetemplate",
            name="name",
            field=models.CharField(
                help_text="Например: Базовый маршрут научной статьи или Маршрут для статьи с патентной проверкой.",
                max_length=255,
                verbose_name="Название",
            ),
        ),
        migrations.AlterField(
            model_name="routesteptemplate",
            name="assignee_kind",
            field=models.CharField(
                choices=[
                    ("author_unit_group", "Роль в подразделении автора"),
                    ("fixed_unit_group", "Роль в указанном подразделении"),
                    ("fixed_user", "Конкретный пользователь"),
                    ("fixed_group", "Общая роль"),
                ],
                help_text="Выберите, как назначается этап: по роли автора в его подразделении, по роли конкретного подразделения, на конкретного пользователя или на общую роль.",
                max_length=32,
                verbose_name="Тип исполнителя",
            ),
        ),
        migrations.AlterField(
            model_name="routesteptemplate",
            name="target_group",
            field=models.ForeignKey(
                blank=True,
                help_text='Заполняется для вариантов с ролью: "Роль в подразделении автора", "Роль в указанном подразделении" или "Общая роль".',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="route_step_templates",
                to="auth.group",
                verbose_name="Роль",
            ),
        ),
        migrations.AlterField(
            model_name="routesteptemplate",
            name="target_unit",
            field=models.ForeignKey(
                blank=True,
                help_text='Заполняется только для варианта "Роль в указанном подразделении". Для варианта "Роль в подразделении автора" подразделение подставится из карточки автора.',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="route_step_templates",
                to="directory.orgunit",
                verbose_name="Подразделение",
            ),
        ),
        migrations.AlterField(
            model_name="routesteptemplate",
            name="target_user",
            field=models.ForeignKey(
                blank=True,
                help_text='Заполняется только для варианта "Конкретный пользователь".',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="route_step_templates",
                to="accounts.user",
                verbose_name="Пользователь",
            ),
        ),
    ]
