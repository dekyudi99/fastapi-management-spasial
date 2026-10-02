import hashlib
import time
from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from config.database import get_db
from models.api_key import ApiKey
from models.users import Users
from services.password_service import verify_password
from services.geoserver_service_client import geoserver_service_client
import json

def mask_api_key(plain_key: str) -> str:
    if plain_key and len(plain_key) > 14:
        return f"{plain_key[:10]}...{plain_key[-4:]}"
    return plain_key or ""

def ensure_user_api_key(user: Users, db: Session) -> ApiKey:
    """
    Memastikan user memiliki 1 API Key aktif lengkap dengan secret key.
    Admin mendapatkan PRIMARY key (gsvc_pk_), sedangkan user biasa mendapatkan STANDARD key (gsvc_sk_).
    """
    api_key = db.query(ApiKey).filter(ApiKey.user_id == user.id).first()

    if not api_key or not api_key.api_key_secret:
        try:
            is_admin = getattr(user, 'role', 'user') == 'admin'
            key_type = "PRIMARY" if is_admin else "STANDARD"
            prefix = "gsvc_pk_" if is_admin else "gsvc_sk_"

            owner_info = json.dumps({
                "user_id": user.id,
                "username": user.username,
                "email": user.email,
                "role": user.role
            })
            res = geoserver_service_client.create_api_key(
                name=f"Key - {user.username}",
                key_type=key_type,
                owner_info=owner_info
            )
            plain_key = res.get("api_key")
            key_id = res.get("id")

            if not api_key:
                api_key = ApiKey(
                    user_id=user.id,
                    name=f"Key - {user.username}",
                    microservice_key_id=key_id,
                    key_prefix=prefix,
                    masked_key=mask_api_key(plain_key),
                    api_key_secret=plain_key,
                    is_active=True
                )
                db.add(api_key)
            else:
                api_key.microservice_key_id = key_id
                api_key.key_prefix = prefix
                api_key.masked_key = mask_api_key(plain_key)
                api_key.api_key_secret = plain_key
                api_key.is_active = True

            db.commit()
            db.refresh(api_key)
        except Exception as e:
            db.rollback()
            print(f"[ensure_user_api_key] Failed to auto-generate key for {user.username}: {e}")

    return api_key

# In-memory cache for verified API keys:
# Maps sha256(x_api_key) -> {"key_id": key.id, "cached_at": timestamp}
_API_KEY_CACHE = {}
_CACHE_TTL = 86400  # 24 hours

def _get_key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

def invalidate_api_key_cache():
    """Clear in-memory cache when keys are modified"""
    _API_KEY_CACHE.clear()

def verify_api_key(
    x_api_key: str = Header(...),
    db: Session = Depends(get_db)
):
    now = time.time()
    fingerprint = _get_key_fingerprint(x_api_key)

    # 1. Check cache first (instant O(1) lookup + fast DB primary key check)
    cached_entry = _API_KEY_CACHE.get(fingerprint)
    if cached_entry and (now - cached_entry["cached_at"] < _CACHE_TTL):
        key = db.query(ApiKey).filter(ApiKey.id == cached_entry["key_id"], ApiKey.is_active == True).first()
        if key:
            return key
        else:
            # Key was deactivated or deleted from DB
            _API_KEY_CACHE.pop(fingerprint, None)

    # 2. If not cached, query active keys ordered by ID desc (newest active keys checked first)
    keys = db.query(ApiKey).filter(ApiKey.is_active == True).order_by(ApiKey.id.desc()).all()

    for key in keys:
        try:
            if key.api_key_hash and verify_password(x_api_key, key.api_key_hash):
                # Cache successful verification
                _API_KEY_CACHE[fingerprint] = {
                    "key_id": key.id,
                    "cached_at": now
                }
                return key
        except Exception:
            continue

    raise HTTPException(
        status_code=401,
        detail="API Key tidak valid."
    )