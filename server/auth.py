"""Authentication routes: register / login / logout / me.

Session-based: on success we store only ``user_id`` in a signed cookie (Starlette
SessionMiddleware). ``session.clear()`` runs before establishing a new identity to
prevent session fixation (point 7).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend import persistence
from server.deps import DbPath, OriginGuard, current_user
from server.ratelimit import auth_rate_limit
from server.schemas import Credentials, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Cookie-auth mutation guard + per-IP throttle on the PBKDF2-heavy auth endpoints.
_AUTH_GUARDS = [OriginGuard, Depends(auth_rate_limit)]


def _login_session(request: Request, user_id: int) -> None:
    request.session.clear()  # anti session-fixation: never reuse a pre-auth session
    request.session["user_id"] = user_id


@router.post("/register", response_model=UserOut, dependencies=_AUTH_GUARDS)
def register(creds: Credentials, request: Request, db_path=DbPath) -> UserOut:
    try:
        user_id = persistence.create_user(creds.username, creds.password, db_path=db_path)
    except ValueError:
        # Username taken. Fine to reveal for registration UX (not a login oracle).
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username already taken")
    _login_session(request, user_id)
    return UserOut(id=user_id, username=creds.username)


@router.post("/login", response_model=UserOut, dependencies=_AUTH_GUARDS)
def login(creds: Credentials, request: Request, db_path=DbPath) -> UserOut:
    user_id = persistence.verify_user(creds.username, creds.password, db_path=db_path)
    if user_id is None:
        # Generic message — don't reveal whether the username exists.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    _login_session(request, user_id)
    return UserOut(id=user_id, username=creds.username)


@router.post("/logout", dependencies=[OriginGuard])
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user_id: int = Depends(current_user), db_path=DbPath) -> UserOut:
    user = persistence.get_user(user_id, db_path=db_path)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return UserOut(id=user["id"], username=user["username"])
