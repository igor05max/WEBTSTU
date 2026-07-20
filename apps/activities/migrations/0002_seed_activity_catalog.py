from django.db import migrations


ACTIVITY_TYPES = [
    ("article", "Статья", "research", False),
    ("monograph", "Монография", "research", False),
    ("grant", "Грант", "research", True),
    ("research_project", "Научно-исследовательская работа", "research", False),
    ("conference", "Конференция или доклад", "research", False),
    ("patent", "Патент или изобретение", "research", False),
    ("software_registration", "Регистрация программы или базы данных", "research", False),
    ("dissertation", "Диссертационная работа", "research", False),
    ("contract_research", "Хоздоговорная работа", "research", False),
    ("textbook", "Учебник", "methodical", False),
    ("teaching_aid", "Учебное пособие", "methodical", False),
    ("methodical_material", "Учебно-методический материал", "methodical", False),
    ("work_program", "Рабочая программа дисциплины", "methodical", False),
    ("online_course", "Электронный курс", "methodical", False),
    ("student_research", "Руководство научной работой студентов", "organisational", False),
    ("olympiad", "Олимпиада или конкурс", "organisational", False),
    ("career_guidance", "Профориентационное мероприятие", "organisational", False),
    ("educational_event", "Организационное или воспитательное мероприятие", "organisational", False),
    ("advanced_training", "Повышение квалификации", "development", False),
    ("professional_retraining", "Профессиональная переподготовка", "development", False),
    ("other", "Другое", "other", False),
]

GRANT_TYPES = [
    ("rnf", "Российский научный фонд (РНФ)"),
    ("president", "Грант или стипендия Президента РФ"),
    ("minobrnauki", "Конкурс Минобрнауки России"),
    ("innovation_fund", "Фонд содействия инновациям"),
    ("regional", "Региональный конкурс"),
    ("university", "Внутривузовский конкурс"),
    ("international", "Международный грант"),
    ("other", "Другой вид гранта"),
]


def seed_catalogs(apps, schema_editor):
    ActivityType = apps.get_model("activities", "ActivityType")
    GrantType = apps.get_model("activities", "GrantType")
    for code, name, area, requires_grant_type in ACTIVITY_TYPES:
        ActivityType.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "area": area,
                "requires_grant_type": requires_grant_type,
                "is_active": True,
            },
        )
    for code, name in GRANT_TYPES:
        GrantType.objects.get_or_create(code=code, defaults={"name": name, "is_active": True})


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_catalogs, migrations.RunPython.noop),
    ]
