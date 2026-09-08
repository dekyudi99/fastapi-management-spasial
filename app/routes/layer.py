from config.geoserver_auth import get_geoserver_connection
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, Form, File, status
from typing import Optional
from models.users import Users
from models.layer import Layer
from models.workspace import Workspace
from models.project import Project
from models.api_key import ApiKey
from services.auth_service import get_current_user
from services.api_key_service import verify_api_key
from config.database import get_db
from sqlalchemy.orm import Session
from sqlalchemy import func
from geoalchemy2.functions import ST_AsGeoJSON, ST_XMin, ST_YMin, ST_XMax, ST_YMax
import os
import shutil
import uuid
import zipfile
from geoalchemy2.shape import from_shape
from shapely.geometry import box
from services.raster_service import get_tiff_metadata, sanitize_tiff_for_geoserver
from services.vector_service import get_vector_metadata, prepare_csv_as_geopackage
from services.hash_id import decode_id
from pydantic import BaseModel
from typing import List, Optional
from services.sld_to_layer import apply_sld_to_layer, generate_raster_sld, assign_style_to_layer

router = APIRouter(prefix="/layer", tags=["Layer"])
geo = get_geoserver_connection()


# Path di dalam container (di-mount dari D:/proyek-gis/... via docker-compose volume)
# GeoServer container juga mount path yang sama, sehingga bisa mengakses file ini.
RASTER_PATH = "/data_raster"
VECTOR_PATH = "/data_vector"

os.makedirs(RASTER_PATH, exist_ok=True)
os.makedirs(VECTOR_PATH, exist_ok=True)

# SUPPORTED_FORMATS = ('.tif', '.tiff', '.geojson', '.zip', '.csv')


# Untuk membuat layer sekaligus store baru di GeoServer
@router.post("/publish-automated", status_code=status.HTTP_201_CREATED)
async def publish_raster(
    workspace_id: str = Form(...),
    layer_name: str = Form(...),
    description: Optional[str] = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):  
    try:
        actual_ws_id = int(workspace_id) if str(workspace_id).isdigit() else decode_id(workspace_id)
        if actual_ws_id is None:
            raise HTTPException(status_code=400, detail="ID Workspace tidak valid!")

        workspace = (
            db.query(Workspace)
            .filter(Workspace.id == actual_ws_id)
            .join(Project)
            .filter(Project.user_id == current_user.id)
            .first()
        )

        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan!")

        file_extension = os.path.splitext(file.filename)[1].lower()
        if file_extension not in ('.tif', '.tiff'):
            raise HTTPException(status_code=400, detail="Hanya file format GeoTIFF (.tif / .tiff) yang didukung!")

        store_name = f"{current_user.username}_{uuid.uuid4()}"

        unique_filename = f"{store_name}{file_extension}"

        file_path = os.path.normpath(os.path.join(RASTER_PATH, unique_filename))
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # sanitize_tiff_for_geoserver(file_path)

        tiff_metadata = get_tiff_metadata(file_path)

        epsg = tiff_metadata["epsg"]
        bounds = tiff_metadata["bbox"]
        width=tiff_metadata["dimensions"]["width"]
        height=tiff_metadata["dimensions"]["height"]

        geom = box(
            bounds.left,
            bounds.bottom,
            bounds.right,
            bounds.top
        )

        meta =Layer(
            workspace_id=workspace.id,
            name=layer_name,
            description=description,
            geoserver_name=store_name,
            epsg=epsg,
            bbox=from_shape(geom, srid=tiff_metadata["epsg"]),
            width=width,
            height=height,
            layer_type="raster",
            data_type="GeoTiff",
            file_path=file_path,
            status="PUBLISHED"
        )

        db.add(meta)
        db.commit()

        success = geo.create_coveragestore(
            layer_name=store_name, 
            path=file_path, 
            workspace=workspace.ws_name
        )
        
        if success:
            print(f"Berhasil! Layer '{layer_name}' siap diakses via WMS.")
            # Cek apakah workspace memiliki default style, jika ada otomatis terapkan!
            default_style_name = f"default_{workspace.ws_name}"
            try:
                assign_style_to_layer(workspace.ws_name, store_name, default_style_name)
                print(f"Otomatis menerapkan default workspace style '{default_style_name}' ke layer '{layer_name}'")
            except Exception as se:
                print(f"Info: Default workspace style belum dibuat atau tidak diterapkan: {se}")

            result = f"Layer '{layer_name}' berhasil dipublikasikan di workspace '{workspace.ws_name}'"
        else:
            result = "Gagal mempublikasikan layer. Pastikan path file benar."
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"Gagal Menyimpan Data, Karena {e}")
        raise HTTPException(status_code=500, detail=f"Gagal Menyimpan Data, Karena {e}")

@router.get("/list")
def list_layers(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        # URL base WMS GeoServer — ambil dari env, fallback ke localhost
        GEOSERVER_URL = os.getenv("GEOSERVER_WMS_URL")

        # 1. Base Query Filter (Layer -> Workspace -> Project -> User)
        base_query = (
            db.query(
                Layer,
                Workspace.ws_name.label("workspace_name"),
                Workspace.name.label("workspace_display_name"),
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

        # Filter workspace_id jika diberikan
        if workspace_id:
            actual_ws_id = int(workspace_id) if str(workspace_id).isdigit() else decode_id(workspace_id)
            if actual_ws_id is not None:
                base_query = base_query.filter(Workspace.id == actual_ws_id)

        # Filter pencarian berdasarkan nama layer (fix: gunakan Layer.name)
        if search:
            base_query = base_query.filter(Layer.name.ilike(f"%{search}%"))

        # 2. Hitung Total Count
        total_count = base_query.count()

        # 3. Hitung Offset dan Ambil Data
        offset = (page - 1) * size
        layers_result = (
            base_query
            .order_by(Layer.created_at.desc())
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
                "workspace_display_name": workspace_display_name or workspace_name,
                "layer_name": layer.name,
                "geoserver_name": layer.geoserver_name,
                "description": layer.description,
                "layer_type": layer.layer_type,          # "raster" / "vector"
                "data_type": layer.data_type,             # "GeoTiff" / "GeoJSON" / "Shapefile" / "CSV"
                "epsg": layer.epsg,
                "width": layer.width,
                "height": layer.height,
                "status": layer.status,
                "bbox": [minx, miny, maxx, maxy] if minx is not None else None,
                # URL WMS siap pakai untuk Leaflet WMSTileLayer
                "wms_url": f"{GEOSERVER_WMS_BASE}/{workspace_name}/wms",
                "created_at": layer.created_at,
            }
            for layer, workspace_name, workspace_display_name, minx, miny, maxx, maxy in layers_result
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
    

class ColorEntryItem(BaseModel):
    quantity: float
    color: str
    opacity: float = 1.0
    label: Optional[str] = ""

class UpdateRasterStyleRequest(BaseModel):
    layer_id: int
    style_type: Optional[str] = "values" # "values", "intervals", or "ramp"
    colors: List[ColorEntryItem]

@router.post("/update-style")
def update_layer_style(
    req: UpdateRasterStyleRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):
    layer = db.query(Layer).filter(Layer.id == req.layer_id).first()
    if not layer:
        raise HTTPException(status_code=404, detail="Layer tidak ditemukan")

    workspace = db.query(Workspace).filter(Workspace.id == layer.workspace_id).first()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace tidak ditemukan")

    style_name = f"style_{layer.geoserver_name}"

    try:
        sld_xml = generate_raster_sld(
            style_name=style_name,
            color_entries=[item.dict() for item in req.colors],
            style_type=req.style_type or "values"
        )
        apply_sld_to_layer(
            workspace=workspace.ws_name,
            layer_name=layer.geoserver_name,
            style_name=style_name,
            sld_xml=sld_xml
        )
        return {
            "success": True,
            "detail": f"Style untuk layer '{layer.name}' berhasil diperbarui!",
            "style_name": style_name
        }
    except Exception as e:
        print(f"Error updating style: {e}")
        raise HTTPException(status_code=500, detail=f"Gagal memperbarui style: {str(e)}")


# Endpoint untuk menghapus layer dari PostGIS, GeoServer, dan disk
@router.delete("/delete/{layer_id}")
def delete_layer(
    layer_id: str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        actual_layer_id = int(layer_id) if str(layer_id).isdigit() else decode_id(layer_id)
        if actual_layer_id is None:
            raise HTTPException(status_code=400, detail="ID Layer tidak valid")

        layer = (
            db.query(Layer)
            .join(Workspace, Workspace.id == Layer.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(
                Layer.id == actual_layer_id,
                Project.user_id == current_user.id
            )
            .first()
        )

        if not layer:
            raise HTTPException(status_code=404, detail="Layer tidak ditemukan")

        workspace = db.query(Workspace).filter(Workspace.id == layer.workspace_id).first()

        # 1. Hapus dari GeoServer jika ada
        if workspace and layer.geoserver_name:
            try:
                geo.delete_coveragestore(coveragestore_name=layer.geoserver_name, workspace=workspace.ws_name)
            except Exception as ge:
                print(f"Peringatan: Gagal menghapus coverage store di GeoServer: {ge}")

            try:
                geo.delete_style(style_name=f"style_{layer.geoserver_name}")
            except Exception:
                pass

        # 2. Hapus file fisik jika ada
        if layer.file_path and os.path.exists(layer.file_path):
            try:
                os.remove(layer.file_path)
            except Exception as fe:
                print(f"Peringatan: Gagal menghapus file raster fisik: {fe}")

        # 3. Hapus dari database
        layer_name = layer.name
        db.delete(layer)
        db.commit()

        return {
            "success": True,
            "detail": f"Layer '{layer_name}' berhasil dihapus!"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal menghapus layer: {str(e)}")
