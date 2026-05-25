from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import QuerySet

from users.models.user_profile import ROLE_ADMIN, ROLE_SUPER_ADMIN


def user_role(user) -> str:
    profile = getattr(user, "profile", None)
    return getattr(profile, "role", "") or ""


def is_super_admin(user) -> bool:
    return user_role(user) == ROLE_SUPER_ADMIN


def is_admin(user) -> bool:
    return user_role(user) == ROLE_ADMIN


def scoped_user_ids(user) -> list[int] | None:
    """Return visible user ids for dashboards/logs, or None for super-admin all-scope."""
    if not user or not getattr(user, "is_authenticated", False):
        return []
    if is_super_admin(user):
        return None
    managed_ids = list(
        get_user_model().objects.filter(profile__created_by=user).values_list("id", flat=True)
    )
    return sorted({int(user.id), *managed_ids})


def apply_user_scope(queryset: QuerySet, user, field: str = "user_id") -> QuerySet:
    if user is None:
        return queryset
    visible_ids = scoped_user_ids(user)
    if visible_ids is None:
        return queryset
    return queryset.filter(**{f"{field}__in": visible_ids})
