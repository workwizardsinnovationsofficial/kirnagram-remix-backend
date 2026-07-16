import os
from fastapi import HTTPException

# Shim to verify access tokens using backend JWT auth helper
try:
    from app.jwt_auth import verify_access_token
except Exception:
    # When running remix-backend standalone, fall back to local jwt helper path
    try:
        from jwt_auth import verify_access_token  # type: ignore
    except Exception as e:
        raise RuntimeError("JWT verification helper not available: " + str(e))


def verify_firebase_token(token: str):
    """Compatibility shim. Accepts a Bearer JWT and returns a dict like Firebase's payload.

    This function replaces Firebase Admin token verification by using the project's
    `verify_access_token` helper which validates our JWTs issued by the backend.
    """
    try:
        payload = verify_access_token(token)
        return {
            "uid": payload.get("sub"),
            "email": payload.get("email"),
            "displayName": payload.get("name") or payload.get("full_name"),
            "photoURL": payload.get("photo_url"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(exc)}") from exc