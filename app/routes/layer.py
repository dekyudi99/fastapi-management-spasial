from config.geoserver_auth import get_geoserver_connection
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, Form, File, status
from typing import Optional, List, Dict, Any
from models.users import Users
from models.layer import Layer
from models.workspace import Workspace
from models.project import Project
from models.api_key import ApiKey
from services.auth_service import get_current_user
from services.api_key_service import verify_api_key
from config.database import get_db, engine
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from geoalchemy2.functions import ST_AsGeoJSON, ST_XMin, ST_YMin, ST_XMax, ST_YMax
import os
from dotenv import load_dotenv

load_dotenv()
from datetime import datetime
import re
import shutil
import uuid
import zipfile
import requests
from requests.auth import HTTPBasicAuth
from geoalchemy2.shape import from_shape
from shapely.geometry import box
from services.raster_service import get_tiff_metadata, sanitize_tiff_for_geoserver
from services.vector_service import (
    get_vector_metadata,
    read_vector_file_to_gdf,
    simplify_vector_gdf,
    publish_vector_to_geoserver_postgis,
)
from services.hash_id import decode_id
from pydantic import BaseModel
from services.sld_to_layer import apply_sld_to_layer, generate_raster_sld, generate_vector_sld, assign_style_to_layer, style_exists_in_geoserver

router = APIRouter(prefix="/layer", tags=["Layer"])
geo = get_geoserver_connection()


# Path di dalam container (di-mount via docker-compose volume)
RASTER_PATH = "/data_raster"
VECTOR_PATH = "/data_vector"

os.makedirs(RASTER_PATH, exist_ok=True)
os.makedirs(VECTOR_PATH, exist_ok=True)

RASTER_FORMATS = ('.tif', '.tiff')
VECTOR_FORMATS = ('.geojson', '.json', '.zip', '.shp', '.gpkg', '.csv', '.kml', '.kmz')
SUPPORTED_FORMATS = RASTER_FORMATS + VECTOR_FORMATS


# Endpoint utama: Otomatis mempublikasikan berkas Raster maupun Vector ke GeoServer
@router.post("/publish-automated", status_code=status.HTTP_201_CREATED)
async def publish_layer(
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
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan atau tidak memiliki akses!")

        file_extension = os.path.splitext(file.filename)[1].lower()
        if file_extension not in SUPPORTED_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"Format berkas '{file_extension}' tidak didukung! Format yang didukung: {', '.join(SUPPORTED_FORMATS)}"
            )

        clean_user = re.sub(r'[^a-z0-9]', '', (current_user.username or "user").lower())[:8]

        # ── KASUS 1: DATA VEKTOR (Shapefile, GeoJSON, GeoPackage, CSV) ────────
        if file_extension in VECTOR_FORMATS:
            table_name = f"vec_{clean_user}_{uuid.uuid4().hex[:10]}"
            unique_filename = f"{table_name}{file_extension}"
            file_path = os.path.normpath(os.path.join(VECTOR_PATH, unique_filename))

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            # Baca file ke GeoPandas GeoDataFrame
            gdf, format_name = read_vector_file_to_gdf(file_path)

            # Lakukan Topology-Preserving Simplification secara otomatis berdasarkan nilai tetap environment
            env_tol = os.getenv("SIMPLIFY_TOLERANCE")
            if not env_tol:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Variabel environment SIMPLIFY_TOLERANCE belum disetel."
                )
            fixed_tolerance = float(env_tol)
            gdf, stats = simplify_vector_gdf(gdf, tolerance=fixed_tolerance, preserve_topology=True)

            # Ambil Bounding Box dari data vektor
            total_bounds = gdf.total_bounds
            geom = box(
                float(total_bounds[0]),
                float(total_bounds[1]),
                float(total_bounds[2]),
                float(total_bounds[3])
            )

            # Publikasikan ke PostGIS dan GeoServer FeatureType
            dominant_geom = stats.get("geometry_type", "Polygon")
            publish_vector_to_geoserver_postgis(
                gdf=gdf,
                table_name=table_name,
                workspace_name=workspace.ws_name,
                title=layer_name,
                geom_type=dominant_geom
            )

            # Simpan metadata Layer ke database
            meta = Layer(
                workspace_id=workspace.id,
                name=layer_name,
                description=description,
                geoserver_name=table_name,
                epsg=4326,
                bbox=from_shape(geom, srid=4326),
                width=None,
                height=None,
                layer_type="vector",
                data_type=format_name,
                file_path=file_path,
                status="PUBLISHED",
                metadata_json={"simplification": stats}
            )

            db.add(meta)
            db.commit()

            wms_base = (os.getenv("GEOSERVER_WMS_URL") or "").rstrip("/")
            return {
                "success": True,
                "detail": f"Layer vektor '{layer_name}' berhasil disederhanakan dan dipublikasikan di workspace '{workspace.ws_name}'!",
                "layer_id": meta.id,
                "layer_type": "vector",
                "data_type": format_name,
                "geoserver_name": table_name,
                "simplification": stats,
                "wms_url": f"{wms_base}/{workspace.ws_name}/wms"
            }

        # ── KASUS 2: DATA RASTER (GeoTIFF .tif / .tiff) ──────────────────────
        store_name = f"{clean_user}_{uuid.uuid4().hex[:10]}"
        unique_filename = f"{store_name}{file_extension}"
        file_path = os.path.normpath(os.path.join(RASTER_PATH, unique_filename))

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        tiff_metadata = get_tiff_metadata(file_path)
        epsg = tiff_metadata["epsg"]
        bounds = tiff_metadata["bbox"]
        width = tiff_metadata["dimensions"]["width"]
        height = tiff_metadata["dimensions"]["height"]

        geom = box(bounds.left, bounds.bottom, bounds.right, bounds.top)

        meta = Layer(
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
            default_style_name = f"default_{workspace.ws_name}"
            try:
                if not style_exists_in_geoserver(default_style_name):
                    generic_sld = generate_raster_sld(
                        style_name=default_style_name,
                        color_entries=[
                            {"quantity": 0,   "color": "#000000", "opacity": 1.0, "label": "Low"},
                            {"quantity": 128, "color": "#7f7f7f", "opacity": 1.0, "label": "Mid"},
                            {"quantity": 255, "color": "#ffffff", "opacity": 1.0, "label": "High"},
                        ],
                        style_type="ramp"
                    )
                    apply_sld_to_layer(
                        workspace=workspace.ws_name,
                        layer_name=store_name,
                        style_name=default_style_name,
                        sld_xml=generic_sld
                    )
                else:
                    assign_style_to_layer(workspace.ws_name, store_name, default_style_name)
            except Exception as se:
                print(f"Info: Style default diterapkan: {se}")

            result = {
                "success": True,
                "detail": f"Layer raster '{layer_name}' berhasil dipublikasikan di workspace '{workspace.ws_name}'!",
                "layer_id": meta.id,
                "layer_type": "raster",
                "data_type": "GeoTiff",
                "geoserver_name": store_name
            }
        else:
            result = {
                "success": False,
                "detail": "Gagal mempublikasikan layer raster ke GeoServer."
            }
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"Gagal Menyimpan Data, Karena {e}")
        raise HTTPException(status_code=500, detail=f"Gagal Menyimpan Data, Karena {e}")


# Endpoint S2S / Internal: Menerbitkan Layer Geosocial (Vektor / Raster) dari Laravel FlowGIS
@router.post("/publish-geosocial", status_code=status.HTTP_201_CREATED)
async def publish_geosocial_layer(
    name: str = Form(...),
    file: UploadFile = File(...),
    workspace_name: Optional[str] = Form("geosocial"),
    simplify_tolerance: Optional[float] = Form(0.00005),
    simplify_enabled: Optional[bool] = Form(True),
    db: Session = Depends(get_db),
):
    try:
        # Pastikan workspace ada di GeoServer
        try:
            geo.create_workspace(workspace_name)
        except Exception:
            pass

        file_ext = os.path.splitext(file.filename)[1].lower()
        clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower()).strip('_')
        timestamp = int(datetime.now().timestamp())
        store_name = f"geo_{clean_name}_{timestamp}"

        wms_public_base = (os.getenv("GEOSERVER_WMS_URL") or "").rstrip("/")
        wms_url = f"{wms_public_base}/{workspace_name}/wms"

        # Simpan file sementara
        temp_dir = os.path.join(VECTOR_PATH if file_ext in VECTOR_FORMATS else RASTER_PATH, "geosocial_uploads")
        os.makedirs(temp_dir, exist_ok=True)
        file_path = os.path.join(temp_dir, f"{store_name}{file_ext}")

        with open(file_path, "wb") as f:
            f.write(await file.read())

        simplification_stats = None

        if file_ext in VECTOR_FORMATS:
            gdf, format_name = read_vector_file_to_gdf(file_path)

            env_tol = os.getenv("SIMPLIFY_TOLERANCE")
            if not env_tol:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Variabel environment SIMPLIFY_TOLERANCE belum disetel."
                )
            fixed_tolerance = float(env_tol)

            gdf, simplification_stats = simplify_vector_gdf(
                gdf, 
                tolerance=fixed_tolerance, 
                preserve_topology=True
            )

            # Deteksi geometri type
            geom_type = gdf.geometry.geom_type.iloc[0] if not gdf.empty else "Polygon"
            table_name = f"vec_{store_name}"

            publish_vector_to_geoserver_postgis(
                gdf=gdf,
                table_name=table_name,
                workspace_name=workspace_name,
                title=name,
                geom_type=geom_type
            )

            layer_name = f"{workspace_name}:{table_name}"

            return {
                "success": True,
                "layer_name": layer_name,
                "workspace": workspace_name,
                "wms_url": wms_url,
                "type": "vector",
                "geom_type": geom_type,
                "table_name": table_name,
                "simplification": simplification_stats,
                "detail": f"Layer vektor '{name}' berhasil disederhanakan dan dipublikasikan ke GeoServer workspace '{workspace_name}'!"
            }

        elif file_ext in RASTER_FORMATS:
            success = geo.create_coveragestore(
                layer_name=store_name, 
                path=file_path, 
                workspace=workspace_name
            )
            if success:
                default_style = f"default_{workspace_name}"
                try:
                    if not style_exists_in_geoserver(default_style):
                        generic_sld = generate_raster_sld(
                            style_name=default_style,
                            color_entries=[
                                {"quantity": 0, "color": "#000000", "opacity": 1.0, "label": "Low"},
                                {"quantity": 128, "color": "#7f7f7f", "opacity": 1.0, "label": "Mid"},
                                {"quantity": 255, "color": "#ffffff", "opacity": 1.0, "label": "High"},
                            ],
                            style_type="ramp"
                        )
                        apply_sld_to_layer(
                            workspace=workspace_name,
                            layer_name=store_name,
                            style_name=default_style,
                            sld_xml=generic_sld
                        )
                    else:
                        assign_style_to_layer(workspace_name, store_name, default_style)
                except Exception as se:
                    print(f"Info Style: {se}")

                layer_name = f"{workspace_name}:{store_name}"
                return {
                    "success": True,
                    "layer_name": layer_name,
                    "workspace": workspace_name,
                    "wms_url": wms_url,
                    "type": "raster",
                    "detail": f"Layer raster '{name}' berhasil dipublikasikan ke GeoServer workspace '{workspace_name}'!"
                }
            else:
                raise HTTPException(status_code=500, detail="Gagal membuat coveragestore di GeoServer.")
        else:
            raise HTTPException(status_code=400, detail=f"Format berkas '{file_ext}' tidak didukung.")

    except HTTPException:
        raise
    except Exception as e:
        print(f"Gagal publish geosocial layer: {e}")
        raise HTTPException(status_code=500, detail=f"Gagal memproses layer: {str(e)}")


# Endpoint Khusus: Menerbitkan Layer Vektor dengan Penyederhanaan Topologi
@router.post("/publish-vector", status_code=status.HTTP_201_CREATED)
async def publish_vector(
    workspace_id: str = Form(...),
    layer_name: str = Form(...),
    description: Optional[str] = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):
    return await publish_layer(
        workspace_id=workspace_id,
        layer_name=layer_name,
        description=description,
        file=file,
        db=db,
        current_user=current_user
    )



@router.get("/list")
def list_layers(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=500),
    search: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        # URL base WMS GeoServer untuk client browser — ambil dari .env (GEOSERVER_WMS_URL)
        # JANGAN gunakan GEOSERVER_URL karena itu hostname internal Docker (http://geoserver:8080)
        wms_base = (os.getenv("GEOSERVER_WMS_URL") or "").rstrip("/")

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
                "wms_url": f"{wms_base}/{workspace_name}/wms",
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
    
    # 2. Definisikan Base URL (diambil dari file .env)
    base_url = (os.getenv("GEOSERVER_WMS_URL") or "").rstrip("/")
    
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

        # 1. Hapus dari GeoServer & PostGIS jika ada
        if workspace and layer.geoserver_name:
            if layer.layer_type == "vector":
                try:
                    # Drop tabel PostGIS
                    with engine.begin() as conn:
                        conn.execute(text(f'DROP TABLE IF EXISTS public."{layer.geoserver_name}" CASCADE;'))
                except Exception as de:
                    print(f"Peringatan: Gagal menghapus tabel PostGIS: {de}")
                try:
                    actual_store = "postgis_geosocial" if workspace.ws_name == "geosocial" else "postgis_store"
                    geoserver_url = os.getenv("GEOSERVER_URL").rstrip("/")
                    requests.delete(
                        f"{geoserver_url}/rest/workspaces/{workspace.ws_name}/datastores/{actual_store}/featuretypes/{layer.geoserver_name}?recurse=true",
                        auth=HTTPBasicAuth(os.getenv("GEOSERVER_USER"), os.getenv("GEOSERVER_PASS"))
                    )
                except Exception:
                    pass
            else:
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
