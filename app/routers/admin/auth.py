import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from ...config import get_settings
from ...dependencies import get_db
from ...models import User
from ...security import SESSION_COOKIE_NAME, create_session_token, hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
logger = logging.getLogger(__name__)
settings = get_settings()
COOKIE_PARAMS = {
    "httponly": True,
    "samesite": "lax",
    "secure": settings.env.lower() != "development",
}


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("admin/login.html", {"request": request})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid credentials")
    try:
        if not verify_password(password, user.password_hash):
            raise HTTPException(status_code=400, detail="Invalid credentials")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response = RedirectResponse(url="/admin/projects", status_code=303)
    token = create_session_token(user.id)
    response.set_cookie(SESSION_COOKIE_NAME, token, **COOKIE_PARAMS)
    return response


@router.get("/signup")
def signup_page(request: Request):
    return templates.TemplateResponse("admin/signup.html", {"request": request})


@router.post("/signup")
def signup(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")
        user = User(email=email, password_hash=hash_password(password))
        db.add(user)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        logger.warning("Duplicate email detected during signup", exc_info=exc)
        raise HTTPException(status_code=400, detail="Email already registered")
    except Exception as exc:  # pragma: no cover - defensive guard for unexpected failures
        db.rollback()
        logger.exception("Unexpected error while creating user")
        raise HTTPException(status_code=500, detail="Signup failed") from exc

    return RedirectResponse(url="/admin/login", status_code=303)


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        samesite=COOKIE_PARAMS["samesite"],
        secure=COOKIE_PARAMS["secure"],
    )
    return response
