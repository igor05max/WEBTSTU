from apps.accounts.access import get_user_display_name, get_user_initials, is_root_admin


def user_shell_context(request):
    user = request.user
    return {
        "can_access_admin": is_root_admin(user),
        "header_user_name": get_user_display_name(user),
        "header_user_initials": get_user_initials(user),
        "header_user_unit": getattr(getattr(user, "org_unit", None), "name", ""),
    }
