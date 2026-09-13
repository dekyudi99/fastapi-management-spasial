import hashlib
import time
from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from config.database import get_db
from models.api_key import ApiKey
from services.password_service import verify_password

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