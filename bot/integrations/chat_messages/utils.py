from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

    from .enums import UserRole
    from .events import MessageEvent


def min_permission_level(
    event: MessageEvent,
    mininmum_role: UserRole,
    *,
    extra_roles: Collection[UserRole] | None = None,
):
    """Check if the user is at or above a permission level."""
    if not extra_roles:
        extra_roles = []
    min_level = mininmum_role.level()
    if any(r.level() >= min_level for r in event.author_roles):
        return True
    return any(r in extra_roles for r in event.author_roles)
