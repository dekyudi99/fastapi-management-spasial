from fastapi import APIRouter, Form, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import JSONResponse
from config.database import get_db
from sqlalchemy.orm import Session
from models.users import Users
from services.password_service import hash_password, verify_password
from services.jwt_service import create_access_token
from services.auth_service import get_current_user
from fastapi.security import OAuth2PasswordRequestForm
import random
from datetime import datetime, timedelta
from models.email_otp import EmailOTP
from services.email_service import send_otp_email_sync

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):  
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")

    if db.query(Users).filter(Users.username == username).first():
        raise HTTPException(status_code=400, detail="Username is already taken.")

    existing_user = db.query(Users).filter(Users.email == email).first()
    if existing_user:
        if existing_user.is_verified:
            raise HTTPException(status_code=400, detail="Email is already registered. Please log in.")
        existing_user.username = username
        existing_user.password = hash_password(password)
        db.commit()
    else:
        user = Users(
            username=username,
            email=email,
            password=hash_password(password),
            is_verified=False
        )
        try:
            db.add(user)
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to save data: {e}")

    # Kirimkan OTP verifikasi pertama kali
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
        "requires_verification": True,
        "email": email,
        "username": username,
        "detail": "Registration successful! A verification OTP has been sent to your email."
    }
    
@router.post("/login")
def login(
    formdata: OAuth2PasswordRequestForm = Depends(),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    username = formdata.username
    password = formdata.password

    try:
        user = db.query(Users).filter(Users.username == username).first()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    if not user:
        raise HTTPException(status_code=404, detail="Incorrect username or password.")
        
    valid = verify_password(password, user.password)
    if not valid:
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    # Jika akun belum diverifikasi: Kirim OTP baru secara otomatis & arahkan frontend ke verifikasi
    if not user.is_verified:
        otp = str(random.randint(100000, 999999))
        expires = datetime.utcnow() + timedelta(minutes=5)

        db.query(EmailOTP).filter(EmailOTP.email == user.email, EmailOTP.type == "register").delete()
        new_otp = EmailOTP(email=user.email, otp=otp, type="register", expires_at=expires)
        db.add(new_otp)
        db.commit()

        if background_tasks:
            background_tasks.add_task(send_otp_email_sync, user.email, otp, "register")
        else:
            send_otp_email_sync(user.email, otp, "register")

        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "success": False,
                "requires_verification": True,
                "email": user.email,
                "username": user.username,
                "detail": "Your account is not verified yet. A new verification OTP has been sent to your email. Please verify to continue."
            }
        )
    
    payload = {
        "sub": user.username,
        "email": user.email,
        "role": user.role
    }
    
    access_token = create_access_token(data=payload)
    
    return {
        "success": True,
        "detail": f"Welcome {user.username}",
        "access_token": access_token,
        "type": "bearer"
    }

@router.post("/send-register-otp")
def send_register_otp(
    email: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    # Cek apakah email sudah terdaftar
    if db.query(Users).filter(Users.email == email).first():
        raise HTTPException(status_code=400, detail="Email is already registered. Please use another email or log in.")
    # Generate 6 Digit OTP
    otp = str(random.randint(100000, 999999))
    expires = datetime.utcnow() + timedelta(minutes=5)
    # Hapus OTP lama dan simpan yang baru
    db.query(EmailOTP).filter(EmailOTP.email == email, EmailOTP.type == "register").delete()
    new_otp = EmailOTP(email=email, otp=otp, type="register", expires_at=expires)
    db.add(new_otp)
    db.commit()
    # Kirim email di background agar user tidak menunggu lama
    if background_tasks:
        background_tasks.add_task(send_otp_email_sync, email, otp, "register")
    else:
        send_otp_email_sync(email, otp, "register")
    return {"success": True, "detail": "OTP code has been sent to your email."}

@router.post("/verify-and-register", status_code=status.HTTP_201_CREATED)
def verify_and_register(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    otp: str = Form(...),
    db: Session = Depends(get_db)
):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")
    # Validasi OTP
    record = db.query(EmailOTP).filter(
        EmailOTP.email == email,
        EmailOTP.otp == otp,
        EmailOTP.type == "register",
        EmailOTP.expires_at > datetime.utcnow()
    ).first()
    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code.")

    existing_user = db.query(Users).filter(Users.email == email).first()
    if existing_user:
        existing_user.username = username
        existing_user.password = hash_password(password)
        existing_user.is_verified = True
    else:
        new_user = Users(
            username=username,
            email=email,
            password=hash_password(password),
            is_verified=True
        )
        db.add(new_user)

    db.delete(record)  # Hapus OTP sekali pakai
    db.commit()
    return {"success": True, "detail": "Registration and email verification successful!"}

@router.post("/verify-email")
def verify_email(
    email: str = Form(...),
    otp: str = Form(...),
    db: Session = Depends(get_db)
):
    record = db.query(EmailOTP).filter(
        EmailOTP.email == email,
        EmailOTP.otp == otp,
        EmailOTP.type == "register",
        EmailOTP.expires_at > datetime.utcnow()
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code.")

    user = db.query(Users).filter(Users.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.is_verified = True
    db.delete(record)
    db.commit()

    # Langsung berikan token agar user otomatis login setelah verifikasi OTP
    payload = {
        "sub": user.username,
        "email": user.email,
        "role": user.role
    }
    access_token = create_access_token(data=payload)

    return {
        "success": True,
        "detail": "Email verified successfully! You are now logged in.",
        "access_token": access_token,
        "type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role
        }
    }

@router.post("/forgot-password/send-otp")
def send_forgot_password_otp(
    email: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    user = db.query(Users).filter(Users.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Email is not registered in our system.")
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


# ── PROFILE & ACCOUNT MANAGEMENT ──────────────────────────────────────────────

@router.get("/me")
def get_my_profile(
    current_user: Users = Depends(get_current_user)
):
    """Mengambil informasi profil akun pengguna saat ini"""
    created_at_val = current_user.created_at.isoformat() if current_user.created_at else None
    return {
        "success": True,
        "data": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "is_verified": current_user.is_verified,
            "created_at": created_at_val
        }
    }


@router.put("/profile")
def update_profile(
    username: str = Form(...),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Memperbarui username pengguna"""
    clean_username = username.strip()
    if not clean_username:
        raise HTTPException(status_code=400, detail="Username cannot be empty.")

    # Cek apakah username sudah dipakai pengguna lain
    existing = db.query(Users).filter(Users.username == clean_username, Users.id != current_user.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username is already taken by another account.")

    current_user.username = clean_username
    db.commit()
    db.refresh(current_user)

    # Perbarui access token dengan username baru
    payload = {
        "sub": current_user.username,
        "email": current_user.email,
        "role": current_user.role
    }
    new_token = create_access_token(data=payload)

    created_at_val = current_user.created_at.isoformat() if current_user.created_at else None

    return {
        "success": True,
        "detail": "Username updated successfully!",
        "access_token": new_token,
        "data": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "is_verified": current_user.is_verified,
            "created_at": created_at_val
        }
    }


@router.post("/change-email/send-otp")
def send_change_email_otp(
    new_email: str = Form(...),
    background_tasks: BackgroundTasks = None,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mengirim kode OTP ke alamat email baru untuk memverifikasi pergantian email"""
    clean_email = new_email.strip().lower()
    if clean_email == current_user.email.lower():
        raise HTTPException(status_code=400, detail="New email cannot be the same as your current email.")

    # Cek apakah email sudah terdaftar di akun lain
    if db.query(Users).filter(Users.email == clean_email, Users.id != current_user.id).first():
        raise HTTPException(status_code=400, detail="This email address is already in use by another account.")

    otp = str(random.randint(100000, 999999))
    expires = datetime.utcnow() + timedelta(minutes=5)

    db.query(EmailOTP).filter(EmailOTP.email == clean_email, EmailOTP.type == "change_email").delete()
    new_otp = EmailOTP(email=clean_email, otp=otp, type="change_email", expires_at=expires)
    db.add(new_otp)
    db.commit()

    if background_tasks:
        background_tasks.add_task(send_otp_email_sync, clean_email, otp, "register")
    else:
        send_otp_email_sync(clean_email, otp, "register")

    return {
        "success": True,
        "detail": f"Verification OTP code has been sent to your new email ({clean_email})."
    }


@router.post("/change-email/verify")
def verify_change_email(
    new_email: str = Form(...),
    otp: str = Form(...),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Verifikasi kode OTP dan resmi mengganti email pengguna"""
    clean_email = new_email.strip().lower()

    record = db.query(EmailOTP).filter(
        EmailOTP.email == clean_email,
        EmailOTP.otp == otp,
        EmailOTP.type == "change_email",
        EmailOTP.expires_at > datetime.utcnow()
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP code.")

    current_user.email = clean_email
    current_user.is_verified = True
    db.delete(record)
    db.commit()
    db.refresh(current_user)

    # Perbarui access token dengan email baru
    payload = {
        "sub": current_user.username,
        "email": current_user.email,
        "role": current_user.role
    }
    new_token = create_access_token(data=payload)

    created_at_val = current_user.created_at.isoformat() if current_user.created_at else None

    return {
        "success": True,
        "detail": "Email address updated and verified successfully!",
        "access_token": new_token,
        "data": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "is_verified": current_user.is_verified,
            "created_at": created_at_val
        }
    }


@router.post("/change-password")
def change_password(
    old_password: str = Form(...),
    new_password: str = Form(...),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mengubah kata sandi akun dengan memvalidasi kata sandi lama terlebih dahulu"""
    if not verify_password(old_password, current_user.password):
        raise HTTPException(status_code=400, detail="Incorrect current password.")

    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters long.")

    if old_password == new_password:
        raise HTTPException(status_code=400, detail="New password cannot be the same as the old password.")

    current_user.password = hash_password(new_password)
    db.commit()

    return {
        "success": True,
        "detail": "Your password has been updated successfully!"
    }
