from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

CHAIR_HEAD_ROLE_NAME = "Заведующий кафедрой"
CHAIR_HEAD_POSITION_NAME = "Заведующий кафедрой"


def is_chair_head_position(position_or_name):
    if position_or_name is None:
        return False
    name = getattr(position_or_name, "name", position_or_name)
    return str(name).strip().lower() == CHAIR_HEAD_POSITION_NAME.lower()


def get_or_create_chair_head_role():
    role, _ = Group.objects.get_or_create(name=CHAIR_HEAD_ROLE_NAME)
    return role


def has_chair_head_role(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "chair_org_unit_id", None):
        return False
    return user.groups.filter(name=CHAIR_HEAD_ROLE_NAME).exists()


def ensure_chair_head_role_for_org_unit(org_unit):
    if org_unit is None:
        return get_or_create_chair_head_role()
    role = get_or_create_chair_head_role()
    org_unit.available_roles.add(role)
    return role


def get_chair_head_candidates(chair_org_unit):
    User = get_user_model()
    if chair_org_unit is None:
        return User.objects.none()
    return (
        User.objects.filter(
            is_active=True,
            chair_org_unit=chair_org_unit,
            groups__name=CHAIR_HEAD_ROLE_NAME,
        )
        .select_related("org_unit", "chair_org_unit", "position")
        .distinct()
    )
