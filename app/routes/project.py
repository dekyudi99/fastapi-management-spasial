from fastapi import APIRouter, Form, HTTPException, status, Depends, Query
from models.project import Project
from models.users import Users
from models.layer import Layer
from models.workspace import Workspace
from models.api_key import ApiKey
from services.auth_service import get_current_user
from config.database import get_db
from sqlalchemy.orm import Session
from sqlalchemy import func
from services.hash_id import decode_id, encode_id

from models.logs import Logs
from services.log_service import create_log

router = APIRouter(prefix="/project", tags=["Project"])

@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    project_name: str = Form(...),
    description: str = Form(...),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    project = Project(
        user_id=current_user.id,
        project_name=project_name,
        description=description
    )

    try:
        db.add(project)
        db.commit()
        db.refresh(project)
        create_log(
            db=db,
            auth_type="JWT",
            user_id=current_user.id,
            project_id=project.id,
            action="PROJECT_CREATE",
            resource_type="PROJECT",
            resource_id=str(project.id),
            resource_name=project.project_name,
            status="SUCCESS",
            client_user_name=current_user.username,
            client_user_email=current_user.email,
        )
        return {
            "success": True,
            "detail": "Project berhasil dibuat"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("")
def get_project(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        total_count = db.query(func.count(Project.id)).filter(Project.user_id == current_user.id).scalar()

        offset = (page - 1) * size

        projects = (
            db.query(
                Project.id,
                Project.description,
                Project.project_name,
                Project.created_at,
                func.count(func.distinct(Workspace.id)).label("workspace_count"),
                func.count(func.distinct(ApiKey.id)).label("api_key_count")
            )
            .outerjoin(Workspace, Workspace.project_id == Project.id)
            .outerjoin(ApiKey, ApiKey.project_id == Project.id)
            .filter(Project.user_id == current_user.id)
            .group_by(Project.id)
            .order_by(Project.created_at.desc())
            .offset(offset)
            .limit(size)
            .all()
        )

        if not projects:
            result = []
        else:
            result = []
            for row in projects:
                result.append({
                    "id": encode_id(row.id),
                    "project_name": row.project_name,
                    "description": row.description,
                    "created_at": row.created_at,
                    "workspace_count": row.workspace_count,
                    "api_key_count": row.api_key_count
                })
        return {
            "success": True,
            "detail": "Project anda berhasil ditampilkan",
            "data": result,
            "pagination": {
                "total": total_count,
                "page": page,
                "size": size,
                "total_pages": (total_count + size -1) // size
            }
        }
    except Exception:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{hashed_id}")
def update_project(
    hashed_id: str,
    project_name: str = Form(...),
    description: str = Form(...),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        id = decode_id(hashed_id)

        project = db.query(Project).filter(Project.id == id, Project.user_id == current_user.id).first()

        if project is None:
            raise HTTPException(status_code=404, detail="Project tidak ada!")       

        project.project_name = project_name
        project.description = description

        db.commit()
        create_log(
            db=db,
            auth_type="JWT",
            user_id=current_user.id,
            project_id=project.id,
            action="PROJECT_UPDATE",
            resource_type="PROJECT",
            resource_id=str(project.id),
            resource_name=project.project_name,
            status="SUCCESS",
            client_user_name=current_user.username,
            client_user_email=current_user.email,
        )

        return {
            "success": True,
            "detail": "Project berhasil diperbarui!"
        }
    except Exception:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Something went wrong!")

@router.get("/{hashed_id}")
def get_detail_project(
    hashed_id: str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        id = decode_id(hashed_id)

        project, workspace_count, api_key_count, layer_count = (
            db.query(
                Project,
                func.count(func.distinct(Workspace.id)).label("workspace_count"),
                func.count(func.distinct(ApiKey.id)).label("api_key_count"),
                func.count(func.distinct(Layer.id)).label("layer_count")
            )
            .outerjoin(Workspace, Workspace.project_id == Project.id)
            .outerjoin(ApiKey, ApiKey.project_id == Project.id)
            .outerjoin(Layer, Layer.workspace_id == Workspace.id)
            .filter(
                Project.id == id,
                Project.user_id == current_user.id
            )
            .group_by(Project.id)
            .first()
        )

        if project is None:
            raise HTTPException(status_code=404, detail="Project tidak ditemukan!")

        return {
            "success": True,
            "detail": "Project berhasil ditampilkan!",
            "data": {
                "id": encode_id(project.id),
                "project_name": project.project_name,
                "description": project.description,
                "created_at": project.created_at,
                "workspace_count": workspace_count,
                "api_key_count": api_key_count,
                "layer_count": layer_count
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{hashed_id}")
def delete_user(
    hashed_id: str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        id = decode_id(hashed_id)
        project = db.query(Project).filter(Project.id == id, Project.user_id == current_user.id).first()

        if project is None:
            raise HTTPException(status_code=404, detail="Project tidak ditemukan!")

        db.delete(project)
        db.commit()

        return {
            "success": True,
            "detail": "Project berhasil dihapus!"
        }
    except HTTPException:
        raise


@router.get("/{hashed_id}/logs")
def get_project_logs(
    hashed_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mengambil riwayat log aktivitas spesifik untuk project ini"""
    try:
        project_id = decode_id(hashed_id)
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project tidak ditemukan!")

        # Jika log masih kosong pada project lama, generate histori awal otomatis
        existing_log_count = db.query(Logs).filter(Logs.project_id == project_id).count()
        if existing_log_count == 0:
            create_log(
                db=db,
                auth_type="JWT",
                user_id=current_user.id,
                project_id=project_id,
                action="PROJECT_CREATE",
                resource_type="PROJECT",
                resource_id=str(project_id),
                resource_name=project.project_name,
                status="SUCCESS",
                client_user_name=current_user.username,
                client_user_email=current_user.email,
            )
            workspaces = db.query(Workspace).filter(Workspace.project_id == project_id).all()
            for ws in workspaces:
                create_log(
                    db=db,
                    auth_type="JWT",
                    user_id=current_user.id,
                    project_id=project_id,
                    action="WORKSPACE_CREATE",
                    resource_type="WORKSPACE",
                    resource_id=str(ws.id),
                    resource_name=ws.name,
                    status="SUCCESS",
                    client_user_name=current_user.username,
                    client_user_email=current_user.email,
                )
                layers = db.query(Layer).filter(Layer.workspace_id == ws.id).all()
                for lyr in layers:
                    create_log(
                        db=db,
                        auth_type="JWT",
                        user_id=current_user.id,
                        project_id=project_id,
                        action="LAYER_PUBLISH",
                        resource_type="LAYER",
                        resource_id=str(lyr.id),
                        resource_name=lyr.layer_name,
                        status="SUCCESS",
                        client_user_name=current_user.username,
                        client_user_email=current_user.email,
                    )
            api_keys = db.query(ApiKey).filter(ApiKey.project_id == project_id).all()
            for ak in api_keys:
                create_log(
                    db=db,
                    auth_type="JWT",
                    user_id=current_user.id,
                    project_id=project_id,
                    action="API_KEY_CREATE",
                    resource_type="API_KEY",
                    resource_id=str(ak.id),
                    resource_name=ak.name,
                    status="SUCCESS",
                    client_user_name=current_user.username,
                    client_user_email=current_user.email,
                )

        query = db.query(Logs).filter(Logs.project_id == project_id).order_by(Logs.created_at.desc())
        total = query.count()
        logs = query.offset((page - 1) * size).limit(size).all()

        data = []
        for log in logs:
            data.append({
                "id": log.id,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "resource_name": log.resource_name,
                "status": log.status,
                "auth_type": log.auth_type,
                "client_user_name": log.client_user_name or current_user.username,
                "client_user_email": log.client_user_email,
                "ip_address": log.ip_address,
                "meta_data": log.meta_data,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })

        return {
            "success": True,
            "detail": "Project logs retrieved successfully.",
            "data": data,
            "pagination": {
                "total": total,
                "page": page,
                "size": size,
                "total_pages": (total + size - 1) // size
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))