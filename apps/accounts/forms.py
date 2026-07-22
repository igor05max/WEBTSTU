from django import forms
from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.accounts.access import is_root_admin
from apps.accounts.models import User


class RootAdminAuthenticationForm(AdminAuthenticationForm):
    error_messages = {
        **AdminAuthenticationForm.error_messages,
        "root_only": "Доступ в админку разрешен только root-пользователю.",
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not is_root_admin(user):
            raise ValidationError(
                self.error_messages["root_only"],
                code="root_only",
            )


class UserRolesByGroupMixin:
    role_help_text = "Выберите одну или несколько ролей пользователя внутри выбранной группы."

    def _is_root_user_instance(self):
        instance_username = getattr(self.instance, "username", "")
        return bool(self.instance.pk and instance_username == settings.ROOT_ADMIN_USERNAME)

    def _setup_user_fields(self):
        for field_name in ("first_name", "last_name", "email"):
            self.fields[field_name].required = True

        self.fields["position"].required = False
        self.fields["org_unit"].required = not self._is_root_user_instance()
        self.fields["chair_org_unit"].required = False
        self.fields["groups"].required = False
        self.fields["position"].label = "Должность"
        self.fields["groups"].label = "Роли"
        self.fields["org_unit"].label = "Группа"
        self.fields["chair_org_unit"].label = "Кафедра"

        org_unit_id = self.data.get("org_unit") or getattr(self.instance, "org_unit_id", None)
        chair_org_unit_id = self.data.get("chair_org_unit") or getattr(self.instance, "chair_org_unit_id", None)
        roles_queryset = Group.objects.order_by("name")
        role_filters = Q()
        if org_unit_id:
            role_filters |= Q(org_units__id=org_unit_id)
        if chair_org_unit_id:
            role_filters |= Q(org_units__id=chair_org_unit_id)
        if role_filters:
            roles_queryset = roles_queryset.filter(role_filters).distinct()
        self.fields["groups"].queryset = roles_queryset
        self.fields["groups"].help_text = (
            self.role_help_text
            + " Для заведующего кафедрой роль может подтягиваться из выбранной кафедры."
        )

    def clean(self):
        cleaned_data = super().clean()
        org_unit = cleaned_data.get("org_unit")
        chair_org_unit = cleaned_data.get("chair_org_unit")
        groups = cleaned_data.get("groups")

        if self._is_root_user_instance():
            return cleaned_data

        if org_unit is None:
            raise ValidationError("Для пользователя нужно выбрать группу.")

        if groups is not None:
            allowed_role_ids = set(org_unit.available_roles.values_list("id", flat=True))
            if chair_org_unit is not None:
                allowed_role_ids.update(chair_org_unit.available_roles.values_list("id", flat=True))
            invalid_roles = [role.name for role in groups if role.id not in allowed_role_ids]
            if invalid_roles:
                raise ValidationError(
                    "Для выбранной группы недоступны роли: " + ", ".join(invalid_roles)
                )

        return cleaned_data


class UserAdminCreationForm(UserRolesByGroupMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "position",
            "org_unit",
            "chair_org_unit",
            "groups",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_user_fields()

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if username == settings.ROOT_ADMIN_USERNAME:
            raise ValidationError("Root-пользователь создается и обслуживается отдельно.")
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = False
        user.is_superuser = False
        user.set_password(settings.DEFAULT_USER_PASSWORD)
        if commit:
            user.save()
            self.save_m2m()
        return user

class UserAdminChangeForm(UserRolesByGroupMixin, forms.ModelForm):
    password = ReadOnlyPasswordHashField(
        label="Пароль",
        help_text=(
            "Используется единый временный пароль, заданный в защищённых "
            "настройках сервера. Значение пароля в интерфейсе не отображается."
        ),
    )

    class Meta:
        model = User
        fields = (
            "username",
            "password",
            "first_name",
            "last_name",
            "email",
            "position",
            "org_unit",
            "chair_org_unit",
            "groups",
            "external_directory_id",
            "is_active",
            "is_staff",
            "is_superuser",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_user_fields()


class UserRegistrationForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "org_unit")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("username", "first_name", "last_name", "email", "org_unit"):
            self.fields[field_name].required = True
        self.fields["org_unit"].label = "Группа"
        self.fields["org_unit"].help_text = "Выберите группу, к которой вы относитесь."

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if username == settings.ROOT_ADMIN_USERNAME:
            raise ValidationError("Этот логин зарезервирован для root-пользователя.")
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.set_password(settings.DEFAULT_USER_PASSWORD)
        if commit:
            user.save()
        return user
