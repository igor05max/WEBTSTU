from django.conf import settings


def is_root_admin(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(
        user.is_active
        and user.is_superuser
        and user.username == settings.ROOT_ADMIN_USERNAME
    )


def get_user_display_name(user):
    if not getattr(user, "is_authenticated", False):
        return ""
    full_name = user.get_full_name().strip()
    return full_name or user.username


def get_user_initials(user):
    if not getattr(user, "is_authenticated", False):
        return ""

    name_parts = [part for part in user.get_full_name().split() if part]
    if name_parts:
        initials = "".join(part[0].upper() for part in name_parts[:2])
        if initials:
            return initials

    username = (user.username or "").strip()
    return username[:2].upper()
