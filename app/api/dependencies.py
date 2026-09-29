from fastapi import Header, HTTPException, Query, status

from app.config import get_settings


async def require_panel_role(x_access_token: str | None = Header(default=None)) -> str:
    """Identify the panel role without exposing credentials to the client."""
    provided = _provided_token(x_access_token, None)
    settings = get_settings()
    for expected, role in (
        (settings.admin_token, "admin"),
        (settings.staff_token, "bartender"),
        (settings.owner_token, "owner"),
        (settings.analyst_token, "analyst"),
    ):
        if expected and provided == expected:
            return role
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid access key")


def _provided_token(header_token: str | None, query_token: str | None) -> str:
    token = header_token or query_token
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token required")
    return token


async def require_staff(
    x_staff_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> str:
    provided = _provided_token(x_staff_token, token)
    settings = get_settings()
    if provided not in {settings.staff_token, settings.admin_token}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required")
    return "admin" if provided == settings.admin_token else "bartender"


async def require_admin(
    x_admin_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> str:
    provided = _provided_token(x_admin_token, token)
    if provided != get_settings().admin_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return "admin"


async def require_owner(
    x_owner_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> str:
    provided = _provided_token(x_owner_token, token)
    settings = get_settings()
    if provided not in {settings.owner_token, settings.admin_token}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")
    return "owner"


async def require_analytics(
    x_owner_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> str:
    provided = _provided_token(x_owner_token, token)
    settings = get_settings()
    analyst_token = getattr(settings, "analyst_token", None)
    roles = {
        settings.admin_token: "admin",
        settings.owner_token: "owner",
    }
    if analyst_token:
        roles[analyst_token] = "analyst"
    role = roles.get(provided)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Analytics access required",
        )
    return role
