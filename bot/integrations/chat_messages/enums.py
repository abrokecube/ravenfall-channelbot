from enum import StrEnum


class UserRole(StrEnum):
    BOT_ADMINISTRATOR = "bot_admin"
    ADMINISTRATOR = "admin"
    MODERATOR = "moderator"
    USER = "user"

    def level(self) -> int:
        return USER_ROLE_HIERARCHY_VALUES.get(self, 0)


USER_ROLE_HIERARCHY: tuple[UserRole | tuple[UserRole, ...], ...] = (
    UserRole.BOT_ADMINISTRATOR,
    UserRole.ADMINISTRATOR,
    UserRole.MODERATOR,
    UserRole.USER,
)
USER_ROLE_HIERARCHY_VALUES: dict[str, int] = {}
for i, u in enumerate(reversed(USER_ROLE_HIERARCHY)):
    if isinstance(u, tuple):
        for su in u:
            USER_ROLE_HIERARCHY_VALUES[su] = i
    else:
        USER_ROLE_HIERARCHY_VALUES[u] = i
