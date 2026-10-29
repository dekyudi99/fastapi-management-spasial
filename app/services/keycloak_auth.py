import os
import logging
import secrets
from typing import Callable, List
import jwt
from jwt import PyJWKClient, PyJWTError
from fastapi import HTTPException, status, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from config.database import get_db
from models.users import Users
from services.api_key_service import ensure_user_api_key
from dotenv import load_dotenv

# Pastikan environment variables selalu ter-reload dari file .env
load_dotenv(override=True)

logger = logging.getLogger("voxagis.keycloak_auth")

# Environment configurations (semua dibaca dari env, tanpa hardcode)
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8081").rstrip("/")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "wefgis").strip()
KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080").rstrip("/")

KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}").strip()
KEYCLOAK_JWKS_URL = os.getenv(
    "KEYCLOAK_JWKS_URL",
    f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
).strip()
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "voxagis").strip()

# Valid application roles for VoxaGIS
VALID_APP_ROLES = set(
    r.strip()
    for r in os.getenv("KEYCLOAK_VALID_ROLES", "user,admin,app-voxagis").split(",")
    if r.strip()
)

# Global JWKS client with cache
_jwks_client: PyJWKClient | None = None


def get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(KEYCLOAK_JWKS_URL, cache_keys=True, max_cached_keys=16)
    return _jwks_client


def extract_token_roles(payload: dict) -> List[str]:
    """
    Ekstrak role dari token:
    1. resource_access[<client_id>].roles (Client roles utama)
    2. realm_access.roles (Realm roles fallback)
    """
    roles = set()
    resource_access = payload.get("resource_access", {})
    if isinstance(resource_access, dict):
        client_access = resource_access.get(KEYCLOAK_CLIENT_ID, {})
        if isinstance(client_access, dict):
            for r in client_access.get("roles", []):
                roles.add(str(r).lower())

    realm_access = payload.get("realm_access", {})
    if isinstance(realm_access, dict):
        for r in realm_access.get("roles", []):
            roles.add(str(r).lower())

    return list(roles)


def verify_keycloak_jwt(token: str) -> dict:
    """
    Validasi Access Token Keycloak:
    1. Tanda tangan RS256 via JWKS
    2. Kedaluwarsa (exp) & Not Before (nbf)
    3. Issuer (iss == KEYCLOAK_ISSUER)
    4. Authorized party (azp == KEYCLOAK_CLIENT_ID) atau aud mencakup client
    5. Pemeriksaan kepemilikan role valid untuk VoxaGIS (user/admin/app-voxagis)
    """
    unauthorized_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token tidak valid atau sudah kedaluwarsa",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=KEYCLOAK_ISSUER,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_aud": False,  # public client tokens contain aud=account
            },
        )
    except jwt.ExpiredSignatureError as e:
        logger.warning(f"Keycloak token expired: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token telah kedaluwarsa. Silakan login kembali.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except (PyJWTError, Exception) as e:
        logger.warning(f"Keycloak JWT signature/validation failed: {e}")
        raise unauthorized_exc from e

    # Validasi azp (Authorized Party) atau aud
    azp = payload.get("azp")
    aud = payload.get("aud")
    is_azp_valid = azp == KEYCLOAK_CLIENT_ID
    is_aud_valid = (isinstance(aud, list) and KEYCLOAK_CLIENT_ID in aud) or (aud == KEYCLOAK_CLIENT_ID)

    if not is_azp_valid and not is_aud_valid:
        logger.warning(f"Invalid azp '{azp}' or aud '{aud}', expected client '{KEYCLOAK_CLIENT_ID}'")
        raise unauthorized_exc

    # Validasi kepemilikan role untuk client ini
    token_roles = extract_token_roles(payload)
    payload["_extracted_roles"] = token_roles

    matching_roles = [r for r in token_roles if r in VALID_APP_ROLES]
    if not matching_roles:
        logger.warning(
            f"User '{payload.get('preferred_username')}' lacks any valid role for {KEYCLOAK_CLIENT_ID}. Roles found: {token_roles}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Anda belum memiliki akses ke aplikasi ini",
        )

    return payload


def resolve_keycloak_user(db: Session, payload: dict) -> Users:
    """
    Pencocokan user lokal sesuai spesifikasi:
    1. Cari berdasarkan keycloak_sub = klaim 'sub'
    2. Jika tidak ada, cari berdasarkan email (case-insensitive) HANYA jika
       email_verified == True dan HANYA ADA SATU baris yang cocok (count == 1).
    3. Jika tetap tidak ada, buat user lokal baru (username unik, password acak NOT NULL).
    4. Perbarui email & nama jika ada perubahan dari token.
    """
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Klaim 'sub' tidak ditemukan di token Keycloak",
        )

    token_roles = payload.get("_extracted_roles") or extract_token_roles(payload)
    is_admin = "admin" in token_roles

    # 1. Cari berdasarkan keycloak_sub
    user = db.query(Users).filter(Users.keycloak_sub == sub).first()
    if user:
        # Perbarui email/username jika ada perubahan
        raw_email = payload.get("email")
        if raw_email:
            clean_email = raw_email.strip().lower()
            if user.email != clean_email:
                user.email = clean_email
                db.commit()
                db.refresh(user)
        return user

    raw_email = payload.get("email")
    email_verified = bool(payload.get("email_verified", False))
    clean_email = raw_email.strip().lower() if raw_email else None

    # 2. Cari berdasarkan email (case-insensitive)
    if clean_email:
        matching_users = db.query(Users).filter(func.lower(Users.email) == clean_email).all()

        if email_verified:
            if len(matching_users) == 1:
                matched_user = matching_users[0]
                # Pastikan keycloak_sub belum dipakai baris lain
                if matched_user.keycloak_sub and matched_user.keycloak_sub != sub:
                    logger.error(
                        f"Conflict: User {matched_user.id} has keycloak_sub '{matched_user.keycloak_sub}' but token sub is '{sub}'"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Konflik identitas akun: keycloak_sub sudah terdaftar pada pengguna lain",
                    )

                matched_user.keycloak_sub = sub
                db.commit()
                db.refresh(matched_user)
                logger.info(f"Linked existing user '{matched_user.email}' to Keycloak sub '{sub}'")
                ensure_user_api_key(matched_user, db)
                return matched_user

            elif len(matching_users) > 1:
                # Konflik email ganda: jangan menebak! Log warning dan tolak untuk mencegah penyerobotan
                logger.warning(
                    f"Multiple ({len(matching_users)}) users found with email '{clean_email}'. Skipping ambiguous auto-link."
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Ditemukan beberapa akun dengan email '{clean_email}'. Hubungi administrator untuk penyelarasan akun.",
                )
        else:
            # Email_verified adalah False, tapi email sudah terdaftar di database lokal
            if len(matching_users) > 0:
                logger.warning(
                    f"Account with email '{clean_email}' exists locally, but Keycloak token email_verified is False. Refusing link."
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Verifikasi email Anda terlebih dahulu.",
                )

    # 3. Buat user lokal baru
    final_email = clean_email or f"{sub[:8]}@keycloak.local"
    base_username = (
        payload.get("preferred_username")
        or (final_email.split("@")[0] if final_email else f"user_{sub[:8]}")
    )

    # Pastikan username unik di tabel users
    candidate_username = base_username
    counter = 1
    while db.query(Users).filter(Users.username == candidate_username).first():
        candidate_username = f"{base_username}_{counter}"
        counter += 1

    # Password diisi nilai acak yang aman (tidak bisa dipakai login manual)
    random_password = secrets.token_urlsafe(32)

    new_user = Users(
        keycloak_sub=sub,
        username=candidate_username,
        email=final_email,
        password=random_password,
        role="admin" if is_admin else "user",
        is_verified=email_verified,
    )

    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        logger.info(f"Provisioned new user '{new_user.username}' from Keycloak sub '{sub}'")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to provision user from Keycloak payload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal menginisialisasi user dari Keycloak: {e}",
        )

    # Otomatis buat GeoServer API key untuk pengguna baru
    try:
        ensure_user_api_key(new_user, db)
    except Exception as e:
        logger.warning(f"Could not auto-generate API key for '{new_user.username}': {e}")

    return new_user


def require_role(required_role: str) -> Callable:
    """
    SATU dependency pusat untuk otorisasi berbasis role Keycloak.
    Dapat digunakan di route manapun: Depends(require_role('admin')).
    Role dievaluasi langsung dari klaim token Keycloak yang telah divalidasi.
    """
    def _role_checker(
        payload: dict = Depends(verify_keycloak_jwt),
    ) -> dict:
        roles = payload.get("_extracted_roles") or extract_token_roles(payload)
        required = required_role.lower()

        # Admin selalu memiliki akses ke role user
        has_access = (required in roles) or ("admin" in roles)

        if not has_access:
            logger.warning(
                f"Access denied: User '{payload.get('preferred_username')}' requires role '{required_role}', but has: {roles}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Akses ditolak: Memerlukan role '{required_role}'",
            )
        return payload

    return _role_checker
