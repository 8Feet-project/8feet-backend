"""
用户人设业务逻辑。
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils import timezone

from users.models.persona import UserPersona
from users.models.user_profile import ROLE_SUPER_ADMIN


def _get_or_create_persona(user) -> UserPersona:
    persona, _ = UserPersona.objects.get_or_create(user=user)
    return persona


def get_user_persona_markdown(user) -> str:
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    persona = UserPersona.objects.filter(user=user).first()
    return str(persona.content_markdown or "").strip() if persona else ""


def should_prompt_persona(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    profile = getattr(user, "profile", None)
    if profile and profile.role == ROLE_SUPER_ADMIN:
        return False
    persona = UserPersona.objects.filter(user=user).first()
    if persona is None:
        return True
    return not persona.content_markdown.strip() and persona.skipped_at is None


def serialize_user_persona(user) -> dict:
    persona = UserPersona.objects.filter(user=user).first()
    content = str(persona.content_markdown or "").strip() if persona else ""
    return {
        "has_persona": bool(content),
        "content_markdown": content,
        "summary": str(persona.summary or "").strip() if persona else "",
        "source_thread_id": persona.source_thread_id if persona else "",
        "model_id": persona.model_id if persona else "",
        "skipped_at": persona.skipped_at.isoformat() if persona and persona.skipped_at else None,
        "updated_at": persona.updated_at.isoformat() if persona else None,
        "should_prompt_persona": should_prompt_persona(user),
    }


def skip_user_persona_prompt(user) -> dict:
    persona = _get_or_create_persona(user)
    persona.skipped_at = timezone.now()
    persona.save(update_fields=["skipped_at", "updated_at"])
    return serialize_user_persona(user)


def clear_user_persona(user) -> dict:
    persona = _get_or_create_persona(user)
    persona.content_markdown = ""
    persona.summary = ""
    persona.source_thread_id = ""
    persona.model_id = ""
    persona.save(update_fields=["content_markdown", "summary", "source_thread_id", "model_id", "updated_at"])
    return serialize_user_persona(user)


def save_user_persona_report(
    *,
    user,
    content_markdown: str,
    source_thread_id: str,
    model_id: str,
) -> UserPersona:
    persona = _get_or_create_persona(user)
    content = str(content_markdown or "").strip()
    persona.content_markdown = content
    persona.summary = _summarize_markdown(content)
    persona.source_thread_id = source_thread_id
    persona.model_id = str(model_id or "")
    persona.skipped_at = None
    persona.save(
        update_fields=[
            "content_markdown",
            "summary",
            "source_thread_id",
            "model_id",
            "skipped_at",
            "updated_at",
        ]
    )
    return persona


def _summarize_markdown(content: str) -> str:
    lines = []
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line.lstrip("-*0123456789. ").strip())
        if len(" ".join(lines)) >= 180:
            break
    summary = " ".join(item for item in lines if item).strip()
    return summary[:240]


def is_first_super_admin(user) -> bool:
    profile = getattr(user, "profile", None)
    if not profile or profile.role != ROLE_SUPER_ADMIN:
        return False
    User = get_user_model()
    first_user = User.objects.order_by("id").first()
    return bool(first_user and first_user.id == user.id)
