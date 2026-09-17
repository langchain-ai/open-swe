"""Dashboard routes for people: the admin listing of who Open SWE knows."""

from typing import Any

from fastapi import APIRouter, Query

from agent.dashboard.deps import ADMIN_DEP
from agent.users.models import User

router = APIRouter(tags=["users"])


@router.get("/admin/users")
async def admin_list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, Any]:
    """One page of users with the identities each one signed in or linked with."""
    users, total = await User.page(offset=(page - 1) * page_size, limit=page_size)
    return {
        "items": [
            {
                "user_id": str(user.id),
                "github_login": user.github_login,
                "email": user.email,
                "slack_user_id": user.slack_user_id or None,
                "display_name": user.display_name,
                "is_admin": user.is_admin,
            }
            for user in users
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
