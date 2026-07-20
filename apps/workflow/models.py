from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models


class RevisionStrategy(models.TextChoices):
    RESTART_CURRENT_STEP = "restart_current_step", "Повторить текущий этап"
    RESTART_ROUTE = "restart_route", "Перезапустить маршрут"


class AssigneeKind(models.TextChoices):
    AUTHOR_UNIT_GROUP = "author_unit_group", "Роль в группе автора"
    AUTHOR_CHAIR_HEAD = "author_chair_head", "Заведующий кафедрой автора"
    FIXED_UNIT_GROUP = "fixed_unit_group", "Роль в указанной группе"
    FIXED_USER = "fixed_user", "Конкретный пользователь"
    FIXED_GROUP = "fixed_group", "Общая роль"


class WorkflowRunStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    ACTIVE = "active", "Активен"
    PAUSED_FOR_REVISION = "paused_for_revision", "На доработке"
    COMPLETED = "completed", "Завершен"
    REJECTED = "rejected", "Отклонен"


class WorkflowStepStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    ACTIVE = "active", "Активен"
    APPROVED = "approved", "Одобрен"
    REJECTED = "rejected", "Отклонен"
    REVISION_REQUESTED = "revision_requested", "Возврат на доработку"
    SKIPPED = "skipped", "Пропущен"


class ApprovalTaskStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    ACTIVE = "active", "Активна"
    APPROVED = "approved", "Одобрена"
    REJECTED = "rejected", "Отклонена"
    REVISION_REQUESTED = "revision_requested", "Возврат на доработку"


class TaskDecisionType(models.TextChoices):
    APPROVE = "approve", "Согласовано"
    REJECT = "reject", "Отклонено"
    REQUEST_REVISION = "request_revision", "На доработку"
    COMMENT = "comment", "Комментарий"


class RouteTemplate(models.Model):
    name = models.CharField(
        max_length=255,
        verbose_name="Название",
        help_text="Например: базовый маршрут для материала или маршрут с дополнительной проверкой.",
    )
    article_type = models.ForeignKey(
        "directory.ArticleType",
        on_delete=models.PROTECT,
        related_name="route_templates",
        null=True,
        blank=True,
        verbose_name="Тип материала",
        help_text="Шаблон можно привязать к конкретному типу материала: статье, тезисам или монографии.",
    )
    direction = models.ForeignKey(
        "directory.Direction",
        on_delete=models.PROTECT,
        related_name="route_templates",
        null=True,
        blank=True,
        verbose_name="Область экспертизы",
        help_text=(
            "Для новых базовых маршрутов поле не заполняется: область влияет только на состав исполнителей внутри шагов. "
            "Поле оставлено для истории и совместимости со старыми маршрутами."
        ),
    )
    priority = models.IntegerField(default=0, verbose_name="Приоритет")
    revision_strategy = models.CharField(
        max_length=32,
        choices=RevisionStrategy.choices,
        default=RevisionStrategy.RESTART_CURRENT_STEP,
        verbose_name="Стратегия доработки",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        ordering = ("direction__name", "article_type__name", "-priority", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("article_type",),
                condition=models.Q(
                    direction__isnull=True,
                    article_type__isnull=False,
                ),
                name="unique_base_route_template_per_article_type",
            )
        ]
        verbose_name = "Шаблон маршрута"
        verbose_name_plural = "Шаблоны маршрутов"

    def __str__(self):
        return self.name


class RouteStepTemplate(models.Model):
    route_template = models.ForeignKey(
        RouteTemplate,
        on_delete=models.CASCADE,
        related_name="step_templates",
        verbose_name="Шаблон маршрута",
    )
    order = models.PositiveIntegerField(verbose_name="Порядок")
    name = models.CharField(max_length=255, verbose_name="Название этапа")
    assignee_kind = models.CharField(
        max_length=32,
        choices=AssigneeKind.choices,
        verbose_name="Тип исполнителя",
        help_text=(
            "Выберите, как назначается этап: по заведующему кафедрой автора "
            "или по роли конкретной группы."
        ),
    )
    target_group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="route_step_templates",
        null=True,
        blank=True,
        verbose_name="Роль",
        help_text=(
            "Заполняется для вариантов с ролью: "
            '"Роль в группе автора", "Роль в указанной группе" или "Общая роль".'
        ),
    )
    target_unit = models.ForeignKey(
        "directory.OrgUnit",
        on_delete=models.PROTECT,
        related_name="route_step_templates",
        null=True,
        blank=True,
        verbose_name="Группа",
        help_text=(
            'Заполняется только для варианта "Роль в указанной группе". '
            'Для варианта "Роль в группе автора" группа подставится из карточки автора.'
        ),
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="route_step_templates",
        null=True,
        blank=True,
        verbose_name="Пользователь",
        help_text='Заполняется только для варианта "Конкретный пользователь".',
    )
    can_reject = models.BooleanField(default=True, verbose_name="Можно отклонить")
    can_request_revision = models.BooleanField(default=True, verbose_name="Можно вернуть на доработку")

    class Meta:
        ordering = ("order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("route_template", "order"),
                name="unique_route_step_order",
            )
        ]
        verbose_name = "Шаг шаблона маршрута"
        verbose_name_plural = "Шаги шаблона маршрута"

    def __str__(self):
        return f"{self.route_template} / {self.order}. {self.name}"

    def clean(self):
        super().clean()
        errors = {}

        if self.assignee_kind == AssigneeKind.AUTHOR_CHAIR_HEAD:
            if self.target_unit_id is not None:
                errors["target_unit"] = "Для заведующего кафедрой автора группа не заполняется."
            if self.target_group_id is not None:
                errors["target_group"] = "Для заведующего кафедрой автора роль не заполняется вручную."
            if self.target_user_id is not None:
                errors["target_user"] = "Для заведующего кафедрой автора пользователь определяется автоматически."
            if errors:
                raise ValidationError(errors)
            return

        has_any_default_assignment = any(
            [self.target_unit_id, self.target_group_id, self.target_user_id]
        )
        if has_any_default_assignment:
            if self.target_unit_id is None:
                errors["target_unit"] = "Нужно выбрать группу."
            if self.target_group_id is None:
                errors["target_group"] = "Нужно выбрать роль."

        if errors:
            raise ValidationError(errors)

        if self.target_unit_id is None or self.target_group_id is None:
            return

        if not self.target_unit.available_roles.filter(id=self.target_group_id).exists():
            raise ValidationError(
                {"target_group": "Эта роль не входит в список ролей выбранной группы."}
            )

        if self.target_user_id is None:
            return

        if self.target_user.org_unit_id != self.target_unit_id:
            raise ValidationError(
                {"target_user": "Пользователь должен относиться к выбранной группе."}
            )

        if not self.target_user.groups.filter(id=self.target_group_id).exists():
            raise ValidationError(
                {"target_user": "У пользователя нет выбранной роли внутри этой группы."}
            )


class RouteStepDirectionAssignment(models.Model):
    step_template = models.ForeignKey(
        RouteStepTemplate,
        on_delete=models.CASCADE,
        related_name="direction_assignments",
        verbose_name="Шаг шаблона",
    )
    direction = models.ForeignKey(
        "directory.Direction",
        on_delete=models.CASCADE,
        related_name="route_step_assignments",
        verbose_name="Область экспертизы",
    )
    target_group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="route_step_direction_assignments",
        verbose_name="Роль",
    )
    target_unit = models.ForeignKey(
        "directory.OrgUnit",
        on_delete=models.PROTECT,
        related_name="route_step_direction_assignments",
        verbose_name="Группа",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="route_step_direction_assignments",
        null=True,
        blank=True,
        verbose_name="Пользователь",
        help_text="Необязательно. Если не указан, задача будет назначена на роль и группу.",
    )

    class Meta:
        ordering = ("step_template_id", "direction__name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("step_template", "direction"),
                name="unique_route_step_direction_assignment",
            )
        ]
        verbose_name = "Назначение шага по области"
        verbose_name_plural = "Назначения шагов по областям"

    def __str__(self):
        return f"{self.step_template} / {self.direction.name}"

    def clean(self):
        super().clean()
        errors = {}

        if self.target_unit_id is None:
            errors["target_unit"] = "Нужно выбрать группу."
        if self.target_group_id is None:
            errors["target_group"] = "Нужно выбрать роль."

        if errors:
            raise ValidationError(errors)

        if not self.target_unit.available_roles.filter(id=self.target_group_id).exists():
            raise ValidationError(
                {"target_group": "Эта роль не входит в список ролей выбранной группы."}
            )

        if self.target_user_id is None:
            return

        if self.target_user.org_unit_id != self.target_unit_id:
            raise ValidationError(
                {"target_user": "Пользователь должен относиться к выбранной группе."}
            )

        if not self.target_user.groups.filter(id=self.target_group_id).exists():
            raise ValidationError(
                {"target_user": "У пользователя нет выбранной роли внутри этой группы."}
            )


class WorkflowRun(models.Model):
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="workflow_runs",
        verbose_name="Заявка",
    )
    route_template = models.ForeignKey(
        RouteTemplate,
        on_delete=models.PROTECT,
        related_name="workflow_runs",
        verbose_name="Шаблон маршрута",
    )
    status = models.CharField(
        max_length=32,
        choices=WorkflowRunStatus.choices,
        default=WorkflowRunStatus.PENDING,
        verbose_name="Статус",
    )
    current_step = models.ForeignKey(
        "workflow.WorkflowStep",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="Текущий шаг",
    )
    awaiting_route_approval = models.BooleanField(
        default=False,
        verbose_name="Ожидает проверки маршрута кафедрой",
    )
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="Запущен")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Запуск маршрута"
        verbose_name_plural = "Запуски маршрутов"

    def __str__(self):
        return f"{self.submission_id} / {self.route_template.name}"


class WorkflowStep(models.Model):
    workflow_run = models.ForeignKey(
        WorkflowRun,
        on_delete=models.CASCADE,
        related_name="steps",
        verbose_name="Запуск маршрута",
    )
    step_template = models.ForeignKey(
        RouteStepTemplate,
        on_delete=models.PROTECT,
        related_name="workflow_steps",
        null=True,
        blank=True,
        verbose_name="Шаблон шага",
    )
    order = models.PositiveIntegerField(verbose_name="Порядок")
    name = models.CharField(max_length=255, verbose_name="Название этапа")
    assignee_kind = models.CharField(
        max_length=32,
        choices=AssigneeKind.choices,
        verbose_name="Тип исполнителя",
    )
    assigned_group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="workflow_steps",
        null=True,
        blank=True,
        verbose_name="Роль",
    )
    assigned_unit = models.ForeignKey(
        "directory.OrgUnit",
        on_delete=models.PROTECT,
        related_name="workflow_steps",
        null=True,
        blank=True,
        verbose_name="Группа",
    )
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="workflow_steps",
        null=True,
        blank=True,
        verbose_name="Пользователь",
    )
    can_reject = models.BooleanField(default=True, verbose_name="Можно отклонить")
    can_request_revision = models.BooleanField(default=True, verbose_name="Можно вернуть на доработку")
    status = models.CharField(
        max_length=32,
        choices=WorkflowStepStatus.choices,
        default=WorkflowStepStatus.PENDING,
        verbose_name="Статус",
    )
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="Запущен")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")

    class Meta:
        ordering = ("workflow_run_id", "order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("workflow_run", "order"),
                name="unique_workflow_step_order",
            )
        ]
        verbose_name = "Шаг маршрута"
        verbose_name_plural = "Шаги маршрута"

    def __str__(self):
        return f"{self.workflow_run_id} / {self.order}. {self.name}"


class ApprovalTask(models.Model):
    workflow_step = models.ForeignKey(
        WorkflowStep,
        on_delete=models.CASCADE,
        related_name="tasks",
        verbose_name="Шаг маршрута",
    )
    status = models.CharField(
        max_length=32,
        choices=ApprovalTaskStatus.choices,
        default=ApprovalTaskStatus.PENDING,
        verbose_name="Статус",
    )
    assigned_group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="approval_tasks",
        null=True,
        blank=True,
        verbose_name="Роль",
    )
    assigned_unit = models.ForeignKey(
        "directory.OrgUnit",
        on_delete=models.PROTECT,
        related_name="approval_tasks",
        null=True,
        blank=True,
        verbose_name="Группа",
    )
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approval_tasks",
        null=True,
        blank=True,
        verbose_name="Пользователь",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name="Активирована")
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="Результат зафиксирован")

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Задача согласования"
        verbose_name_plural = "Задачи согласования"

    def __str__(self):
        return f"{self.workflow_step} / {self.get_status_display()}"


class TaskDecision(models.Model):
    task = models.ForeignKey(
        ApprovalTask,
        on_delete=models.CASCADE,
        related_name="decisions",
        verbose_name="Задача",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="task_decisions",
        verbose_name="Кто зафиксировал результат",
    )
    decision = models.CharField(
        max_length=32,
        choices=TaskDecisionType.choices,
        verbose_name="Результат",
    )
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        ordering = ("created_at", "id")
        verbose_name = "Результат этапа"
        verbose_name_plural = "Результаты этапов"

    def __str__(self):
        return f"{self.task_id} / {self.decision}"
