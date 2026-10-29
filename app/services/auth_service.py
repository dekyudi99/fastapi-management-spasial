import os
import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from config.database import get_db
from models.users import Users
from services.keycloak_auth import (
    verify_keycloak_jwt,
    resolve_keycloak_user,
    require_role,
    extract_token_roles,
)

logger = logging.getLogger("voxagis.auth_service")

# SSO adalah autentikasi utama wajib
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login"
)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Users:
    """
    Dependency pusat untuk mengambil user yang sedang login via Keycloak SSO token.
    1. Memverifikasi validitas token (signature, expiration, issuer, azp/aud, role).
    2. Menemukan atau menyelaraskan user lokal (Users model).
    """
    # 1. Validasi token Keycloak SSO
    payload = verify_keycloak_jwt(token)
    user = resolve_keycloak_user(db, payload)

    # Pastikan user lokal membawa informasi role dari token untuk kemudahan akses di endpoint
    user.token_roles = payload.get("_extracted_roles") or extract_token_roles(payload)
    return user


__all__ = ["get_current_user", "require_role", "verify_keycloak_jwt", "resolve_keycloak_user"]