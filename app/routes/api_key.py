import json
from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session
from models.api_key import ApiKey
from models.users import Users
from services.auth_service import get_current_user
from config.database import get_db
from services.geoserver_service_client import geoserver_service_client
from services.log_service import create_log

router = APIRouter(prefix="/api-key", tags=["API Key Management"])

from services.api_key_service import ensure_user_api_key, mask_api_key

@router.get("/me", status_code=status.HTTP_200_OK)
def get_my_api_key(
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Mengambil 1 API Key milik user.
    Jika user belum memiliki API Key, sistem otomatis men-generate 1 Standard Key di microservice.
    """
    api_key = ensure_user_api_key(current_user, db)
    if not api_key:
        raise HTTPException(status_code=500, detail="Gagal menginisialisasi API Key.")

    disk_usage = None
    if api_key.is_active and api_key.api_key_secret:
        try:
            disk_usage = geoserver_service_client.get_key_usage(api_key.api_key_secret)
        except Exception:
            pass

    return {
        "id": api_key.id,
        "name": api_key.name,
        "key_prefix": api_key.key_prefix,
        "masked_key": api_key.masked_key,
        "full_key": api_key.api_key_secret if api_key.is_active else None,  # Cloudflare style: full key ready to copy
        "plain_key": api_key.api_key_secret if api_key.is_active else None, # Backward compatibility
        "is_active": api_key.is_active,
        "created_at": api_key.created_at,
        "disk_usage": disk_usage
    }

@router.get("/test-connection")
def test_geoserver_connection(current_user: Users = Depends(get_current_user)):
    """Uji konektivitas ringan langsung ke GeoServer Microservice."""
    return geoserver_service_client.test_connection()

@router.post("/refresh", status_code=status.HTTP_200_OK)
def refresh_my_api_key(
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Me-refresh API Key pengguna.
    API Key lama akan dinonaktifkan di microservice, dan API Key baru akan diterbitkan.
    """
    api_key = db.query(ApiKey).filter(ApiKey.user_id == current_user.id).first()

    # Cegah user merefresh kunci jika sedang dinonaktifkan oleh Admin
    if api_key and not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key Anda sedang dinonaktifkan oleh Administrator. Anda tidak dapat me-refresh kunci sampai diaktifkan kembali oleh Admin."
        )

    try:
        # Nonaktifkan kunci lama di microservice jika ada
        if api_key and api_key.microservice_key_id:
            try:
                geoserver_service_client.deactivate_api_key(api_key.microservice_key_id)
            except Exception:
                pass

        is_admin = getattr(current_user, 'role', 'user') == 'admin'
        key_type = "PRIMARY" if is_admin else "STANDARD"
        prefix = "gsvc_pk_" if is_admin else "gsvc_sk_"

        owner_info = json.dumps({
            "user_id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role
        })
        res = geoserver_service_client.create_api_key(
            name=f"Key - {current_user.username}",
            key_type=key_type,
            owner_info=owner_info
        )
        plain_key = res.get("api_key")
        key_id = res.get("id")

        if not api_key:
            api_key = ApiKey(
                user_id=current_user.id,
                name=f"Key - {current_user.username}",
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

        return {
            "success": True,
            "detail": "API Key berhasil direfresh! Kunci lama telah tidak berlaku.",
            "api_key": plain_key,
            "full_key": plain_key,
            "masked_key": api_key.masked_key,
            "is_active": api_key.is_active
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal me-refresh API Key: {e}")