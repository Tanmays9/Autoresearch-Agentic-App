import hmac

from fastapi import Header, HTTPException, status

from .config import get_settings


def require_local_token(
    authorization: str | None = Header(default=None),
    x_local_token: str | None = Header(default=None),
) -> None:
    expected = get_settings().local_token()
    supplied = x_local_token
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid local token")

