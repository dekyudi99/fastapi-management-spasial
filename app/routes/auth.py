import os
import random
from datetime import datetime, timedelta
from fastapi import APIRouter, Form, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from config.database import get_db
from models.users import Users
from models.email_otp import EmailOTP
from services.password_service import hash_password, verify_password
from services.jwt_service import create_access_token
from services.auth_service import get_current_user
from services.email_service import send_otp_email_sync
from services.api_key_service import ensure_user_api_key

router = APIRouter(prefix="/auth", tags=["Auth"])

# Flag env AUTH_LOCAL_ENABLED (default: False)
AUTH_LOCAL_ENABLED = os.getenv(
    "AUTH_LOCAL_ENABLED",
    os.getenv("LEGACY_AUTH_ENABLED", "false")
).strip().lower() in ("true", "1", "yes")


def require_local_auth():
    """Memastikan alur autentikasi lokal hanya dapat diakses jika AUTH_LOCAL_ENABLED aktif"""
    if not AUTH_LOCAL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Autentikasi lokal dinonaktifkan. Silakan gunakan Single Sign-On (Keycloak SSO).",
        )


# ── PENDAFTARAN & LOGIN LOKAL (DILINDUNGI FLAG AUTH_LOCAL_ENABLED) ─────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    require_local_auth()

    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")

    existing_user = db.query(Users).filter(Users.email == email).first()
    if existing_user and existing_user.is_verified:
        raise HTTPException(status_code=400, detail="Email is already registered. Please log in.")

    existing_username = db.query(Users).filter(Users.username == username).first()
    if existing_username:
        if not existing_user or existing_username.id != existing_user.id:
            raise HTTPException(status_code=400, detail="Username is already taken.")

    if existing_user:
        existing_user.username = username
        existing_user.password = hash_password(password)
        db.commit()
    else:
        user = Users(
            username=username,
            email=email,
            password=hash_password(password),
            role="user",
            is_verified=False
        )
        try:
            db.add(user)
            db.commit()
            db.refresh(user)
            ensure_user_api_key(user, db)
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    otp = str(random.randint(100000, 999999))
    expires = datetime.utcnow() + timedelta(minutes=5)

    db.query(EmailOTP).filter(EmailOTP.email == email, EmailOTP.type == "register").delete()
    new_otp = EmailOTP(email=email, otp=otp, type="register", expires_at=expires)
    db.add(new_otp)
    db.commit()

    if background_tasks:
        background_tasks.add_task(send_otp_email_sync, email, otp, "register")
    else:
        send_otp_email_sync(email, otp, "register")

    return {
        "success": True,
        "detail": "Account registered. Verification OTP code has been sent to your email."
    }


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    require_local_auth()

    login_identifier = form_data.username.strip()
    user = db.query(Users).filter(
        (Users.username == login_identifier) | (Users.email == login_identifier.lower())
    ).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(form_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your email has not been verified. Please verify your account first."
        )

    payload = {
        "sub": user.username,
        "email": user.email,
        "role": user.role
    }
    token = create_access_token(data=payload)

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "username": user.username,
        "email": user.email,
        "is_verified": user.is_verified
    }


@router.post("/send-register-otp")
def send_register_otp(
    email: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    require_local_auth()

    clean_email = email.strip().lower()
    existing_user = db.query(Users).filter(Users.email == clean_email).first()
    if existing_user and existing_user.is_verified:
        raise HTTPException(status_code=400, detail="This email is already registered and verified. Please log in directly.")

    otp = str(random.randint(100000, 999999))
    expires = datetime.utcnow() + timedelta(minutes=5)

    db.query(EmailOTP).filter(EmailOTP.email == clean_email, EmailOTP.type == "register").delete()
    new_otp = EmailOTP(email=clean_email, otp=otp, type="register", expires_at=expires)
    db.add(new_otp)
    db.commit()

    if background_tasks:
        background_tasks.add_task(send_otp_email_sync, clean_email, otp, "register")
    else:
        send_otp_email_sync(clean_email, otp, "register")

    return {
        "success": True,
        "detail": f"Registration OTP has been sent to {clean_email}."
    }


@router.post("/verify-and-register", status_code=status.HTTP_201_CREATED)
def verify_and_register(
    email: str = Form(...),
    otp: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    require_local_auth()

    clean_email = email.strip().lower()
    clean_username = username.strip()

    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")

    record = db.query(EmailOTP).filter(
        EmailOTP.email == clean_email,
        EmailOTP.otp == otp,
        EmailOTP.type == "register",
        EmailOTP.expires_at > datetime.utcnow()
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code.")

    existing_email = db.query(Users).filter(Users.email == clean_email).first()
    if existing_email and existing_email.is_verified:
        raise HTTPException(status_code=400, detail="Email is already registered. Please log in.")

    existing_username = db.query(Users).filter(Users.username == clean_username).first()
    if existing_username:
        if not existing_email or existing_username.id != existing_email.id:
            raise HTTPException(status_code=400, detail="Username is already taken. Please choose another username.")

    if existing_email:
        existing_email.username = clean_username
        existing_email.password = hash_password(password)
        existing_email.is_verified = True
        user = existing_email
    else:
        user = Users(
            username=clean_username,
            email=clean_email,
            password=hash_password(password),
            role="user",
            is_verified=True
        )
        db.add(user)

    db.delete(record)
    db.commit()
    db.refresh(user)

    ensure_user_api_key(user, db)

    payload = {"sub": user.username, "email": user.email, "role": user.role}
    token = create_access_token(data=payload)

    return {
        "success": True,
        "detail": "Account registered and email verified successfully!",
        "access_token": token,
        "token_type": "bearer",
        "username": user.username,
        "email": user.email,
        "role": user.role
    }


@router.post("/verify-email")
def verify_email(
    email: str = Form(...),
    otp: str = Form(...),
    db: Session = Depends(get_db)
):
    require_local_auth()

    clean_email = email.strip().lower()
    record = db.query(EmailOTP).filter(
        EmailOTP.email == clean_email,
        EmailOTP.otp == otp,
        EmailOTP.type == "register",
        EmailOTP.expires_at > datetime.utcnow()
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code.")

    user = db.query(Users).filter(Users.email == clean_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User account not found.")

    user.is_verified = True
    db.delete(record)
    db.commit()

    return {"success": True, "detail": "Email verified successfully. You can now log in."}


@router.post("/forgot-password/send-otp")
def forgot_password_send_otp(
    email: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    require_local_auth()

    user = db.query(Users).filter(Users.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Email is not registered.")

    otp = str(random.randint(100000, 999999))
    expires = datetime.utcnow() + timedelta(minutes=5)

    db.query(EmailOTP).filter(EmailOTP.email == email, EmailOTP.type == "reset_password").delete()
    new_otp = EmailOTP(email=email, otp=otp, type="reset_password", expires_at=expires)
    db.add(new_otp)
    db.commit()

    if background_tasks:
        background_tasks.add_task(send_otp_email_sync, email, otp, "reset_password")
    else:
        send_otp_email_sync(email, otp, "reset_password")

    return {"success": True, "detail": "Password reset OTP code has been sent to your email."}


@router.post("/forgot-password/reset")
def reset_password(
    email: str = Form(...),
    otp: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db)
):
    require_local_auth()

    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters long.")

    record = db.query(EmailOTP).filter(
        EmailOTP.email == email,
        EmailOTP.otp == otp,
        EmailOTP.type == "reset_password",
        EmailOTP.expires_at > datetime.utcnow()
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code.")

    user = db.query(Users).filter(Users.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.password = hash_password(new_password)
    db.delete(record)
    db.commit()

    return {"success": True, "detail": "Password updated successfully. Please log in again."}


# ── PROFILE & SESSION DATA ───────────────────────────────────────────────────

@router.get("/me")
def get_me(
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Mengambil profil pengguna saat ini.
    Mendukung token Keycloak SSO dan legacy token.
    """
    token_roles = getattr(current_user, "token_roles", [current_user.role or "user"])
    api_key_record = ensure_user_api_key(current_user, db)
    s2s_key = api_key_record.api_key_secret if api_key_record else None

    return {
        "success": True,
        "data": {
            "id": current_user.id,
            "keycloak_sub": current_user.keycloak_sub,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "roles": token_roles,
            "is_verified": current_user.is_verified,
            "s2s_key": s2s_key,
            "masked_key": api_key_record.masked_key if api_key_record else None,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        }
    }
