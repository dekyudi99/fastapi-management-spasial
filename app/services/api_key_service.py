from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from config.database import get_db
from models.api_key import ApiKey
from services.password_service import verify_password

def verify_api_key(
    x_api_key: str = Header(...),
    db: Session = Depends(get_db)
):
    keys = db.query(ApiKey).filter(ApiKey.is_active == True).all()

    for key in keys:
        try:
            if key.api_key_hash and verify_password(x_api_key, key.api_key_hash):
                return key
        except Exception:
            continue

    raise HTTPException(
        status_code=401,
        detail="API Key tidak valid."
    )