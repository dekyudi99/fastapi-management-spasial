from fastapi import APIRouter, Form, HTTPException, status, Depends
from models.api_key import ApiKey
from models.project import Project
from models.users import Users
from services.auth_service import get_current_user
from config.database import get_db
from sqlalchemy.orm import Session
from services.password_service import hash_password
import secrets
from sqlalchemy import func
from services.hash_id import encode_id, decode_id

router = APIRouter(prefix="/api-key", tags=["Api Key"])

@router.post("/{hashed_id}", status_code=status.HTTP_201_CREATED)
def create_api_key(
    hashed_id: str,
    name: str = Form(...),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    id = decode_id(hashed_id)

    plain_api_key = "agis_sk_"+secrets.token_urlsafe(32)
    hash_key = hash_password(plain_api_key)
    api_key = ApiKey(
        project_id=id,
        name=name,
        api_key_hash=hash_key,
    )

    try: 
        project = db.query(Project).filter(Project.id == id, Project.user_id == current_user.id).first()

        if project is None:
            raise HTTPException(status_code=404, detail="Project yang anda maksud tidak ada!")
        
        db.add(api_key)
        db.commit()
        return {
            "success": True,
            "detail": "Api Key berhasil dibuat!",
            "api_key": plain_api_key
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{hashed_id}")
def get_all_api_key(
    hashed_id: str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        id = decode_id(hashed_id)

        api_key = (
            db.query(ApiKey.id, ApiKey.name, ApiKey.api_key_hash, ApiKey.created_at)
            .join(Project)
            .filter( ApiKey.project_id == id, Project.user_id == current_user.id)
            .all()
        )

        if not api_key:
            raise HTTPException(status_code=200, detail="Tidak ada API Key!")

        list_key = []

        for row in api_key:
            list_key.append({
                "id": encode_id(row.id),
                "name": row.name,
                "api_key_hash": row.api_key_hash,
                "created_at": row.created_at
            })

        return {
            "success": True,
            "detail": "Berhasil menampilkan daftar API Key anda!",
            "data": list_key
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{hashed_id}")
def delete_api_key(
    hashed_id: str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        id = decode_id(hashed_id)

        apikey = (
            db.query(ApiKey)
            .join(Project)
            .filter(ApiKey.id == id, Project.user_id == current_user.id)
            .first()
        )

        if apikey is None:
            raise HTTPException(status_code=404, detail="Api Key tidak ditemukan!")

        db.delete(apikey)
        db.commit()

        return {
            "success": True,
            "detail": f"Api Key {apikey.name} berhasil dihapus"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Something went wrong. {e}")