from __future__ import annotations

from uuid import UUID

from fastapi import Request

from app.api.errors import AuthError


class ApiKeyResolver:
    def __init__(self, key_to_user: dict[str, UUID]) -> None:
        self._key_to_user = dict(key_to_user)

    def resolve(self, key: str | None) -> UUID | None:
        if not key:
            return None
        return self._key_to_user.get(key)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._key_to_user


def extract_bearer(header_value: str | None) -> str | None:
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


async def resolve_user_id(request: Request, resolver: ApiKeyResolver) -> UUID:
    cached = getattr(request.state, "user_id", None)
    if isinstance(cached, UUID):
        return cached
    token = extract_bearer(request.headers.get("authorization"))
    user_id = resolver.resolve(token)
    if user_id is None:
        raise AuthError()
    request.state.user_id = user_id
    return user_id
