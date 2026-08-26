from config.geoserver_auth import get_geoserver_connection
from fastapi import APIRouter, Form, Depends, HTTPException, status, Query
from services.auth_service import get_current_user
from models.users import Users
from models.layer import Layer
from models.project import Project
from models.workspace import Workspace
from config.database import get_db
from sqlalchemy.orm import Session
import secrets
from sqlalchemy import desc, func
from datetime import datetime, timedelta, timezone
from services.hash_id import encode_id, decode_id

router = APIRouter(prefix="/workspace", tags=["Workspace"])
geo = get_geoserver_connection()

# Untuk membuat workspace baru di GeoServer
@router.post("/create/{id}", status_code=status.HTTP_201_CREATED)
def create_workspace(
    id: int,
    name_workspace: str = Form(...),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        project = db.query(Project).filter(Project.id == id, Project.user_id == current_user.id).first()

        if project is None:
            raise HTTPException(status_code=404, detail="Project tidak ditemukan!")

        workspace_name = f"ws_{secrets.token_hex(4)}"
        success = geo.create_workspace(workspace=workspace_name)

        if success:
            workspace = Workspace(
                project_id=id,
                name=name_workspace,
                ws_name=workspace_name
            )

            db.add(workspace)
            db.commit()

            return {
                "success": True,
                "detail": "Workspace berhasil dibuat!"
            }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# Fungsi untuk menampilkan daftar workspace yang sudah ada
@router.get("/list/{hashed_id}")
def list_workspaces(
    hashed_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        id = decode_id(hashed_id)

        total_count = db.query(func.count(Workspace.id)).join(Project).filter(Project.user_id == current_user.id).scalar()
                
        offset = (page - 1) * size

        workspaces = (
            db.query(
                Workspace,
                func.count(func.distinct(Layer.id)).label("layer_count")
            )
            .join(Project)
            .outerjoin(Layer, Layer.workspace_id == Workspace.id)
            .filter( Project.id == id, Project.user_id == current_user.id)
            .group_by(Workspace.id)
            .order_by(Workspace.created_at.desc())
            .offset(offset)
            .limit(size)
            .all()
        )

        result = []
        
        for workspaces, layer_count in workspaces:
            result.append({
                "id": encode_id(workspaces.id),
                "name": workspaces.name,
                "created_at": workspaces.created_at,
                "layer_count": layer_count
            })

        return {
            "success": True,
            "detail": "Berhasil menampilkan daftar workspace anda!",
            "data": result,
            "pagination": {
                "total": total_count,
                "page": page,
                "size": size,
                "total_pages": (total_count + size -1) // size
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# Fungsi untuk melihat default workspace
@router.get("/{hashed_id}")
def get_detail_workspace(
    hashed_id = str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        id = decode_id(hashed_id)

        workspace = (
            db.query(Workspace)
            .join(Project)
            .filter(
                Workspace.id == id,
                Project.user_id == current_user.id
            )
            .first()
        )

        if workspace is None:
            HTTPException(status_code=404, detail="Workspace tidak ditemukan!")

        result = {
            "id": encode_id(workspace.id),
            "name": workspace.name,
            "created_at": workspace.created_at,
        }

        return {
            "success": True,
            "detail": "Get detail workspace is successfulL",
            "data": result
        }
    except HTTPException:
        raise
    except Exception as e:
        HTTPException(status_code=500, detail=str(e))

# Fungsi untuk menghapus workspace
@router.delete("/delete/{hashed_id}")
def delete_workspace(
    hashed_id: str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        id = decode_id(hashed_id)

        workspace = (
            db.query(Workspace)
            .join(Project)
            .filter(Workspace.id == id, Project.user_id == current_user.id)
            .first()
        )
        
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan!")

        db.delete(workspace)
        db.commit()

        success = geo.delete_workspace(workspace=workspace.ws_name)
        
        if success:
            result = (f"BERHASIL! Workspace '{workspace.name}' telah dihapus.")
        else:
            result = ("GAGAL! Pastikan nama workspace benar dan GeoServer Docker Anda sudah berjalan.")
            
        return result
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recently/{hashed_id}")
def get_recently(
    hashed_id: str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        id = decode_id(hashed_id)
        seven_days_ago = datetime.now(timezone.utc) - timedelta(7)

        recent_workspace = (
            db.query(
                Workspace,
                func.count(func.distinct(Layer.id)).label("layer_count")
            )
            .join(Project)
            .outerjoin(Layer, Layer.workspace_id == Workspace.id)
            .filter(
                Project.id == id,
                Project.user_id == current_user.id,
                Workspace.created_at >= seven_days_ago
            )
            .group_by(
                Workspace.id,
                Project.created_at
            )
            .order_by(Project.created_at.desc())
            .limit(5)
            .all()
        )

        result = []

        for workspace, layer_count in recent_workspace:
            result.append({
                "id": encode_id(workspace.id),
                "name": workspace.name,
                "ws_name": workspace.ws_name,
                "created_at": workspace.created_at,
                "layer_count": layer_count
            })

        return {
            "success": True,
            "detail": "Berhasil menampilkan daftar workspace terbaru anda!",
            "data": result
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))