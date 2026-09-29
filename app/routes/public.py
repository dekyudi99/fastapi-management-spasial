from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional
import secrets
import os
import uuid
import re
import copy

from config.database import get_db
from models.project import Project
from models.workspace import Workspace
from models.layer import Layer
from models.users import Users
from services.hash_id import encode_id, decode_id
from services.auth_service import get_current_user
from services.geoserver_service_client import geoserver_service_client
from services.sld_to_layer import apply_sld_to_layer, generate_raster_sld

geo = geoserver_service_client

router = APIRouter(prefix="/public", tags=["Public"])


class CloneWorkspaceRequest(BaseModel):
    target_project_id: str
    new_workspace_name: Optional[str] = None

ForkWorkspaceRequest = CloneWorkspaceRequest


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC PROJECTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/projects")
def get_public_projects(
    page: int = Query(1, ge=1),
    size: int = Query(12, ge=1, le=100),
    search: str = Query("", description="Filter by project name"),
    db: Session = Depends(get_db),
):
    """Daftar semua project yang di-set publik oleh semua user."""
    query = (
        db.query(
            Project,
            Users.username,
            func.count(func.distinct(Workspace.id)).label("workspace_count"),
            func.count(func.distinct(Layer.id)).label("layer_count"),
        )
        .join(Users, Users.id == Project.user_id)
        .outerjoin(Workspace, Workspace.project_id == Project.id)
        .outerjoin(Layer, Layer.workspace_id == Workspace.id)
        .filter(Project.visibility == "public")
        .group_by(Project.id, Users.username)
        .order_by(Project.created_at.desc())
    )

    if search:
        query = query.filter(Project.project_name.ilike(f"%{search}%"))

    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()

    return {
        "success": True,
        "data": [
            {
                "id": encode_id(p.id),
                "project_name": p.project_name,
                "description": p.description,
                "visibility": p.visibility,
                "owner": username,
                "workspace_count": workspace_count,
                "layer_count": layer_count,
                "created_at": p.created_at,
            }
            for p, username, workspace_count, layer_count in rows
        ],
        "pagination": {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
        },
    }


@router.get("/projects/{hashed_id}")
def get_public_project_detail(
    hashed_id: str,
    db: Session = Depends(get_db),
):
    """Detail project publik beserta daftar workspace-nya."""
    from services.hash_id import decode_id

    project_id = decode_id(hashed_id)
    project = db.query(Project).filter(
        Project.id == project_id, Project.visibility == "public"
    ).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project tidak ditemukan atau bersifat private.")

    owner = db.query(Users).filter(Users.id == project.user_id).first()

    workspaces = (
        db.query(Workspace, func.count(func.distinct(Layer.id)).label("layer_count"))
        .outerjoin(Layer, Layer.workspace_id == Workspace.id)
        .filter(Workspace.project_id == project_id, Workspace.visibility == "public")
        .group_by(Workspace.id)
        .order_by(Workspace.created_at.desc())
        .all()
    )

    return {
        "success": True,
        "data": {
            "id": encode_id(project.id),
            "project_name": project.project_name,
            "description": project.description,
            "owner": owner.username if owner else "unknown",
            "created_at": project.created_at,
            "workspaces": [
                {
                    "id": encode_id(ws.id),
                    "name": ws.name,
                    "layer_count": layer_count,
                    "created_at": ws.created_at,
                }
                for ws, layer_count in workspaces
            ],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC WORKSPACES
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/workspaces")
def get_public_workspaces(
    page: int = Query(1, ge=1),
    size: int = Query(12, ge=1, le=100),
    search: str = Query("", description="Filter by workspace name"),
    db: Session = Depends(get_db),
):
    """Semua workspace yang di-set publik dari project publik."""
    query = (
        db.query(
            Workspace,
            Project.project_name,
            Users.username,
            func.count(func.distinct(Layer.id)).label("layer_count"),
        )
        .join(Project, Project.id == Workspace.project_id)
        .join(Users, Users.id == Project.user_id)
        .outerjoin(Layer, Layer.workspace_id == Workspace.id)
        .filter(Workspace.visibility == "public", Project.visibility == "public")
        .group_by(Workspace.id, Project.project_name, Users.username)
        .order_by(Workspace.created_at.desc())
    )

    if search:
        query = query.filter(Workspace.name.ilike(f"%{search}%"))

    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()

    return {
        "success": True,
        "data": [
            {
                "id": encode_id(ws.id),
                "name": ws.name,
                "project_name": project_name,
                "owner": username,
                "layer_count": layer_count,
                "created_at": ws.created_at,
            }
            for ws, project_name, username, layer_count in rows
        ],
        "pagination": {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
        },
    }


@router.get("/workspaces/{hashed_id}/layers")
def get_public_workspace_layers(
    hashed_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Layer-layer di dalam workspace publik."""
    from services.hash_id import decode_id

    workspace_id = decode_id(hashed_id)
    workspace = (
        db.query(Workspace)
        .join(Project, Project.id == Workspace.project_id)
        .filter(
            Workspace.id == workspace_id,
            Workspace.visibility == "public",
            Project.visibility == "public",
        )
        .first()
    )

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace tidak ditemukan atau bersifat private.")

    total = db.query(func.count(Layer.id)).filter(Layer.workspace_id == workspace_id).scalar()
    layers = (
        db.query(Layer)
        .filter(Layer.workspace_id == workspace_id)
        .order_by(Layer.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    return {
        "success": True,
        "workspace": {
            "id": encode_id(workspace.id),
            "name": workspace.name,
        },
        "data": [
            {
                "id": encode_id(layer.id),
                "layer_name": layer.name,
                "layer_type": layer.layer_type if hasattr(layer, "layer_type") else None,
                "created_at": layer.created_at,
            }
            for layer in layers
        ],
        "pagination": {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": (total + size - 1) // size,
        },
    }


@router.post("/workspaces/{hashed_id}/clone")
@router.post("/workspaces/{hashed_id}/fork")
def clone_public_workspace(
    hashed_id: str,
    req: CloneWorkspaceRequest,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Salin/Clone workspace publik ke dalam project milik user sendiri."""
    try:
        source_ws_id = decode_id(hashed_id)
        source_ws = (
            db.query(Workspace)
            .join(Project, Project.id == Workspace.project_id)
            .filter(
                Workspace.id == source_ws_id,
                Workspace.visibility == "public",
                Project.visibility == "public"
            )
            .first()
        )
        if not source_ws:
            raise HTTPException(status_code=404, detail="Workspace publik tidak ditemukan atau bersifat private!")

        target_proj_id = decode_id(req.target_project_id)
        target_project = (
            db.query(Project)
            .filter(Project.id == target_proj_id, Project.user_id == current_user.id)
            .first()
        )
        if not target_project:
            raise HTTPException(status_code=404, detail="Target project tidak ditemukan atau bukan milik Anda!")

        # Buat workspace GeoServer baru
        new_ws_name = f"ws_{secrets.token_hex(4)}"
        geoserver_service_client.create_workspace(new_ws_name)

        new_name = req.new_workspace_name.strip() if req.new_workspace_name else f"{source_ws.name} (Clone)"

        cloned_workspace = Workspace(
            project_id=target_project.id,
            name=new_name,
            ws_name=new_ws_name,
            visibility="private",
            metadata_json=copy.deepcopy(source_ws.metadata_json) if source_ws.metadata_json else {}
        )
        db.add(cloned_workspace)
        db.commit()
        db.refresh(cloned_workspace)

        # ── CLONE SEMUA LAYER DARI WORKSPACE SUMBER ─────────────────────────
        source_layers = db.query(Layer).filter(Layer.workspace_id == source_ws.id).all()
        cloned_count = 0
        clean_user = re.sub(r'[^a-zA-Z0-9_]', '_', current_user.username)

        for lyr in source_layers:
            try:
                new_store_name = f"{clean_user}_{uuid.uuid4().hex[:10]}"
                lyr_meta = copy.deepcopy(lyr.metadata_json) if isinstance(lyr.metadata_json, dict) else {}

                if lyr.layer_type == "raster":
                    # Terbitkan coverage store di GeoServer workspace baru jika file ada
                    if lyr.file_path and os.path.exists(lyr.file_path):
                        geoserver_service_client.create_coveragestore(
                            store_name=new_store_name,
                            raster_path=lyr.file_path,
                            workspace_name=new_ws_name
                        )

                        # Terapkan custom style/symbology jika ada
                        symbology = lyr_meta.get("symbology")
                        if symbology and symbology.get("classes"):
                            try:
                                layer_style_name = f"style_{new_store_name}"
                                sld_xml = generate_raster_sld(
                                    style_name=layer_style_name,
                                    color_entries=symbology.get("classes", []),
                                    style_type=symbology.get("style_type", "intervals")
                                )
                                apply_sld_to_layer(
                                    workspace=new_ws_name,
                                    layer_name=new_store_name,
                                    style_name=layer_style_name,
                                    sld_xml=sld_xml
                                )
                            except Exception as e_style:
                                print(f"[Clone Style Error] {e_style}")

                        cloned_layer = Layer(
                            workspace_id=cloned_workspace.id,
                            name=lyr.name,
                            description=lyr.description,
                            geoserver_name=new_store_name,
                            epsg=lyr.epsg,
                            bbox=lyr.bbox,
                            width=lyr.width,
                            height=lyr.height,
                            layer_type=lyr.layer_type,
                            data_type=lyr.data_type,
                            file_path=lyr.file_path,
                            status="PUBLISHED",
                            metadata_json=lyr_meta
                        )
                        db.add(cloned_layer)
                        cloned_count += 1

                elif lyr.layer_type == "vector":
                    cloned_layer = Layer(
                        workspace_id=cloned_workspace.id,
                        name=lyr.name,
                        description=lyr.description,
                        geoserver_name=lyr.geoserver_name,
                        epsg=lyr.epsg,
                        bbox=lyr.bbox,
                        width=lyr.width,
                        height=lyr.height,
                        layer_type=lyr.layer_type,
                        data_type=lyr.data_type,
                        file_path=lyr.file_path,
                        status="PUBLISHED",
                        metadata_json=lyr_meta
                    )
                    db.add(cloned_layer)
                    cloned_count += 1
            except Exception as e_layer:
                print(f"[Clone Layer Error] {e_layer}")

        db.commit()

        return {
            "success": True,
            "detail": f"Workspace '{source_ws.name}' beserta {cloned_count} layer berhasil di-clone ke project '{target_project.project_name}'!",
            "data": {
                "id": encode_id(cloned_workspace.id),
                "name": cloned_workspace.name,
                "project_id": encode_id(target_project.id),
                "cloned_layers": cloned_count
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
