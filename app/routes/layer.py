from config.geoserver_auth import get_geoserver_connection
from fastapi import APIRouter,HTTPException, Depends, Query
from models.users import Users
from models.layer import Layer
from models.workspace import Workspace
from models.project import Project
from services.auth_service import get_current_user
from config.database import get_db
from sqlalchemy.orm import Session
from sqlalchemy import func
from geoalchemy2.functions import ST_AsGeoJSON, ST_XMin, ST_YMin, ST_XMax, ST_YMax
import json

router = APIRouter(prefix="/layer", tags=["Layer"])
geo = get_geoserver_connection()

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional

@router.get("/list")
def list_layers(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None), # Optional: buat fitur search nama layer
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        # 1. Base Query Filter (Layer -> Workspace -> Project -> User)
        base_query = (
            db.query(
                Layer,
                Workspace.ws_name.label("workspace_name"),
                func.ST_XMin(Layer.bbox).label("minx"),
                func.ST_YMin(Layer.bbox).label("miny"),
                func.ST_XMax(Layer.bbox).label("maxx"),
                func.ST_YMax(Layer.bbox).label("maxy"),
            )
            .join(Workspace, Workspace.id == Layer.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(
                Project.user_id == current_user.id,
            )
        )

        # Optional: Tambah filter pencarian jika parameter search diisi
        if search:
            base_query = base_query.filter(Layer.layer_name.ilike(f"%{search}%"))

        # 2. Hitung Total Count
        total_count = base_query.count()

        # 3. Hitung Offset dan Ambil Data
        offset = (page - 1) * size
        layers_result = (
            base_query
            .order_by(Layer.created_at.desc()) # Urutkan dari yang paling baru dibuat
            .offset(offset)
            .limit(size)
            .all()
        )

        # 4. Format Hasil Response
        result = [
            {
                "id": layer.id,
                "workspace_id": layer.workspace_id,
                "workspace_name": workspace_name,
                "layer_name": layer.name,
                "geoserver_name": layer.geoserver_name,
                "description": layer.description,
                "layer_type": getattr(layer, "layer_type", None), # misal: Polygon / Point
                "epsg": layer.epsg,
                "width": layer.width,
                "height": layer.height,
                "bbox": [minx, miny, maxx, maxy] if minx is not None else None,
                "created_at": layer.created_at,
                # tambahkan field GeoServer jika ada, misal: layer.geoserver_layer_name / url
            }
            for layer, workspace_name, minx, miny, maxx, maxy in layers_result
        ]

        return {
            "success": True,
            "detail": "Daftar layer berhasil ditampilkan",
            "data": result,
            "pagination": {
                "total": total_count,
                "page": page,
                "size": size,
                "total_pages": (total_count + size - 1) // size if size else 0
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Untuk melihat detail metadata sebuah layer tertentu
@router.get("/layer/{layer_name}")
def get_layer_metadata(
    layer_name: str,
    current_user: Users = Depends(get_current_user)
):
    layer = geo.get_layer(layer_name)
    return layer

# Layer preview URL format:
@router.get("/layers/preview")
def get_layer_preview(
    current_user: Users = Depends(get_current_user)
):
    # 1. Ambil metadata dari PostGIS berdasarkan ID
    # data = db.query(RasterMetadata).filter(id=id).first()
    
    # 2. Definisikan Base URL (Bisa diatur di file .env untuk VPS)
    base_url = "http://localhost:8080/geoserver"
    
    workspace = "ikya_auto_test" # Sesuai folder di GeoServer Anda
    layer_name = "singaraja"      # Diambil dari kolom layer_name di DB
    
    # 3. Bentuk Link Preview secara dinamis
    preview_link = (
        f"{base_url}/{workspace}/wms?service=WMS&version=1.1.0"
        f"&request=GetMap&layers={workspace}:{layer_name}"
        f"&format=image/openlayers" 
    )
    
    return {"preview_url": preview_link}