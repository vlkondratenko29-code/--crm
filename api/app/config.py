from dataclasses import dataclass
import os

@dataclass(frozen=True)
class AdminUser:
    username: str
    role: str = "admin"

ADMINS = (
    AdminUser(username="jokwq"),
    AdminUser(username="Nodari777"),
)

def is_admin(username: str) -> bool:
    return username.lstrip("@").lower() in {u.username.lower() for u in ADMINS}
