import json
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from config.database import get_db
from models.users import Users
from models.api_key import ApiKey
from services.auth_service import get_current_user
from services.geoserver_service_client import geoserver_service_client

router = APIRouter(prefix="/admin", tags=["Admin Management"])

def get_current_admin_user(current_user: Users = Depends(get_current_user)) -> Users:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak: Operasi ini membutuhkan hak akses Administrator."
        )
    return current_user

class UserUpdateRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None  # "admin" atau "user"
    is_verified: Optional[bool] = None


# ==========================================================
# 1. USER MANAGEMENT
# ==========================================================

@router.get("/users")
def get_all_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin: Users = Depends(get_current_admin_user)
):
    query = db.query(Users)
    if search:
        s = f"%{search}%"
        query = query.filter((Users.username.ilike(s)) | (Users.email.ilike(s)))

    total = query.count()
    offset = (page - 1) * size
    users = query.order_by(Users.id.desc()).offset(offset).limit(size).all()

    result = []
    for u in users:
        # Ambil info API Key user jika ada
        user_key = db.query(ApiKey).filter(ApiKey.user_id == u.id).first()
        ws_count = 0

        result.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "role": u.role,
            "is_verified": u.is_verified,
            "created_at": str(u.created_at) if u.created_at else None,
            "workspaces_count": ws_count,
            "api_key": {
                "id": user_key.id if user_key else None,
                "masked_key": user_key.masked_key if user_key else None,
                "key_prefix": user_key.key_prefix if user_key else None,
                "is_active": user_key.is_active if user_key else False,
                "microservice_key_id": user_key.microservice_key_id if user_key else None,
                "full_key": user_key.api_key_secret if (user_key and user_key.api_key_secret) else None
            } if user_key else None
        })

    return {
        "success": True,
        "total": total,
        "page": page,
        "size": size,
        "data": result
    }

@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    admin: Users = Depends(get_current_admin_user)
):
    user = db.query(Users).filter(Users.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")

    if payload.username is not None:
        user.username = payload.username
    if payload.email is not None:
        user.email = payload.email
    if payload.role is not None:
        if payload.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="Role harus 'admin' atau 'user'.")
        user.role = payload.role
    if payload.is_verified is not None:
        user.is_verified = payload.is_verified

    db.commit()
    db.refresh(user)

    return {
        "success": True,
        "detail": "Data user berhasil diperbarui.",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "is_verified": user.is_verified
        }
    }

@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: Users = Depends(get_current_admin_user)
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Anda tidak dapat menghapus akun Anda sendiri.")

    user = db.query(Users).filter(Users.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")

    # Nonaktifkan API Key di microservice jika ada
    user_key = db.query(ApiKey).filter(ApiKey.user_id == user_id).first()
    if user_key and user_key.microservice_key_id:
        try:
            geoserver_service_client.deactivate_api_key(user_key.microservice_key_id)
        except Exception:
            pass

    db.delete(user)
    db.commit()

    return {
        "success": True,
        "detail": f"User {user.username} dan seluruh datanya berhasil dihapus."
    }

# ==========================================================
# 2. USER API KEY MANAGEMENT (ADMIN OVERRIDE)
# ==========================================================

@router.post("/users/{user_id}/api-key/toggle")
def toggle_user_api_key(
    user_id: int,
    db: Session = Depends(get_db),
    admin: Users = Depends(get_current_admin_user)
):
    api_key = db.query(ApiKey).filter(ApiKey.user_id == user_id).first()
    if not api_key:
        raise HTTPException(
            status_code=404, 
            detail="User belum memiliki API Key. Silakan buatkan API Key baru terlebih dahulu."
        )

    new_status = not api_key.is_active
    api_key.is_active = new_status

    if api_key.microservice_key_id:
        try:
            geoserver_service_client.toggle_api_key(api_key.microservice_key_id)
        except Exception as e:
            pass

    db.commit()

    return {
        "success": True,
        "is_active": api_key.is_active,
        "detail": f"Status API Key user diubah menjadi {'AKTIF' if api_key.is_active else 'NONAKTIF'}."
    }

@router.post("/keys/{key_id}/toggle")
def toggle_microservice_api_key(
    key_id: str,
    db: Session = Depends(get_db),
    admin: Users = Depends(get_current_admin_user)
):
    api_key = db.query(ApiKey).filter(ApiKey.microservice_key_id == key_id).first()
    if api_key:
        api_key.is_active = not api_key.is_active
        db.commit()

    geoserver_service_client.toggle_api_key(key_id)
    return {"success": True, "detail": "Status API Key microservice berhasil diubah."}

@router.post("/users/{user_id}/api-key/refresh")
@router.post("/users/{user_id}/api-key/generate")
def refresh_user_api_key(
    user_id: int,
    db: Session = Depends(get_db),
    admin: Users = Depends(get_current_admin_user)
):
    user = db.query(Users).filter(Users.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")

    api_key = db.query(ApiKey).filter(ApiKey.user_id == user_id).first()
    is_new_creation = (api_key is None)

    if api_key and api_key.microservice_key_id:
        try:
            geoserver_service_client.deactivate_api_key(api_key.microservice_key_id)
        except Exception:
            pass

    is_admin_user = (getattr(user, 'role', 'user') == 'admin')
    key_type = "PRIMARY" if is_admin_user else "STANDARD"
    prefix = "gsvc_pk_" if is_admin_user else "gsvc_sk_"

    owner_info = json.dumps({
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "generated_by_admin": admin.username
    })

    res = geoserver_service_client.create_api_key(
        name=f"Key - {user.username}",
        key_type=key_type,
        owner_info=owner_info
    )
    plain_key = res.get("api_key")
    key_id = res.get("id")
    masked = f"{plain_key[:10]}...{plain_key[-4:]}" if plain_key and len(plain_key) > 14 else plain_key

    if not api_key:
        api_key = ApiKey(
            user_id=user.id,
            name=f"Key - {user.username}",
            microservice_key_id=key_id,
            key_prefix=prefix,
            masked_key=masked,
            api_key_secret=plain_key,
            is_active=True
        )
        db.add(api_key)
    else:
        api_key.microservice_key_id = key_id
        api_key.key_prefix = prefix
        api_key.masked_key = masked
        api_key.api_key_secret = plain_key
        api_key.is_active = True

    db.commit()
    db.refresh(api_key)

    detail_msg = (
        f"API Key baru berhasil dibuatkan untuk {user.username}!"
        if is_new_creation else
        f"API Key untuk {user.username} berhasil direfresh!"
    )

    return {
        "success": True,
        "detail": detail_msg,
        "api_key": plain_key,
        "full_key": plain_key,
        "masked_key": api_key.masked_key,
        "is_active": api_key.is_active
    }

# ==========================================================
# 3. AUDIT LOGS & GEOSERVER SYSTEM PROXY
# ==========================================================

@router.get("/logs")
def get_system_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: Optional[str] = Query(None),
    admin: Users = Depends(get_current_admin_user)
):
    """
    Mengambil jejak audit logs aktivitas langsung dari GeoServer Microservice.
    """
    return geoserver_service_client.get_audit_logs(
        action=action,
        limit=limit,
        offset=offset
    )

@router.get("/geoserver/overview")
def get_geoserver_overview(
    db: Session = Depends(get_db),
    admin: Users = Depends(get_current_admin_user)
):
    """
    Mengambil status dan daftar workspace langsung dari GeoServer Microservice.
    Enrich data API key dengan data dynamic user dari database lokal (Users table).
    """
    status_info = geoserver_service_client.get_status()
    workspaces = geoserver_service_client.list_workspaces()
    keys = geoserver_service_client.list_api_keys()

    users_list = db.query(Users).all()
    user_by_id = {u.id: u for u in users_list}
    user_by_username = {u.username.lower(): u for u in users_list}

    enriched_keys = []
    for k in keys:
        if k.get("name") == "astragis-core":
            continue

        owner_info_str = k.get("owner_info") or "{}"
        try:
            owner_info = json.loads(owner_info_str) if isinstance(owner_info_str, str) else dict(owner_info_str)
        except Exception:
            owner_info = {}

        user_id = owner_info.get("user_id")
        username = owner_info.get("username")

        u = None
        if user_id and user_id in user_by_id:
            u = user_by_id[user_id]
        elif username and username.lower() in user_by_username:
            u = user_by_username[username.lower()]

        if u:
            owner_info["user_id"] = u.id
            owner_info["username"] = u.username
            owner_info["email"] = u.email
            owner_info["role"] = u.role
            owner_info["is_verified"] = u.is_verified
            k["owner_info"] = json.dumps(owner_info)

            # Dinamiskan key_type sesuai role di database
            if u.role == "admin":
                k["key_type"] = "PRIMARY"
                k["key_prefix"] = "gsvc_pk_"
            else:
                k["key_type"] = "STANDARD"
                k["key_prefix"] = "gsvc_sk_"

        enriched_keys.append(k)

    return {
        "status": status_info,
        "workspaces": workspaces,
        "registered_api_keys": enriched_keys
    }
