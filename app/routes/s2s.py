"""
S2S (System-to-System) Endpoints
================================
Endpoint ini digunakan untuk integrasi antar sistem (contoh: FlowGIS / Laravel → Management Spatial).
Autentikasi menggunakan API Key (header: X-API-Key: agis_sk_...), bukan JWT/email-password.

Endpoints:
    1. POST /s2s/publish-from-url — Publish GeoTIFF langsung dari URL (Google Earth Engine / Cloud Storage)
    2. POST /s2s/publish          — Publish GeoTIFF via file upload (multipart/form-data)
    3. GET  /s2s/layers           — Query daftar layer pengguna/proyek (filter per client_user_id)
"""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, Form, File, Query, Request, status
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from geoalchemy2.shape import from_shape
from shapely.geometry import box
import os
import shutil
import uuid
import json
import re
import httpx

from config.database import get_db
from config.geoserver_auth import get_geoserver_connection
from models.layer import Layer
from models.workspace import Workspace
from models.project import Project
from models.api_key import ApiKey
from models.layer_group import LayerGroup, LayerGroupLayer
from services.layer_group_service import create_geoserver_layergroup, delete_geoserver_layergroup
from services.api_key_service import verify_api_key
from services.hash_id import decode_id
from services.raster_service import get_tiff_metadata, validate_single_band
from services.log_service import create_log
from services.sld_to_layer import (
    apply_sld_to_layer,
    generate_raster_sld,
    assign_style_to_layer,
    style_exists_in_geoserver,
)

router = APIRouter(prefix="/s2s", tags=["System-to-System"])
geo = get_geoserver_connection()

RASTER_PATH = "/data_raster"
os.makedirs(RASTER_PATH, exist_ok=True)


# ── Pydantic Request Models ───────────────────────────────────────────────────

class S2SPublishFromUrlRequest(BaseModel):
    model_config = {"extra": "allow"}

    workspace_id: Any = Field(
        ...,
        description="ID atau Hashed ID workspace tujuan"
    )
    layer_name: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="Nama layer yang ditampilkan di peta/aplikasi"
    )
    description: Optional[str] = Field(
        "",
        description="Deskripsi singkat mengenai layer"
    )
    download_url: str = Field(
        ...,
        description="URL langsung download file GeoTIFF (contoh: Google Earth Engine getPixels URL)"
    )
    style_sld: Optional[str] = Field(
        None,
        description="Dokumen XML SLD siap pakai (contoh: dari FlowGIS)"
    )
    style: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Array rules ColorEntry jika tidak menyediakan XML SLD"
    )
    client_user_id: Optional[Any] = Field(
        None,
        description="ID user dari sistem klien (contoh: ID pengguna di Laravel / FlowGIS)"
    )
    client_user_email: Optional[str] = Field(
        None,
        description="Email pengguna di sistem klien (opsional)"
    )
    client_user_name: Optional[str] = Field(
        None,
        description="Nama pengguna di sistem klien (opsional)"
    )
    statistics: Optional[Dict[str, Any]] = Field(
        None,
        description="Statistik analisis spasial (contoh: avg_elevation, risk_distribution)"
    )
    legends: Optional[Dict[str, Any]] = Field(
        None,
        description="Struktur legenda warna dan label"
    )
    maps: Optional[Dict[str, Any]] = Field(
        None,
        description="Daftar URL tile layer tambahan"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Metadata JSON kustom"
    )
    extra_metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Metadata kustom lainnya"
    )


class S2SCreateLayerGroupRequest(BaseModel):
    model_config = {"extra": "allow"}

    workspace_id: Any = Field(
        ...,
        description="ID atau Hashed ID workspace"
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="Nama teknis layer group di GeoServer"
    )
    title: Optional[str] = Field(
        None,
        description="Judul display layer group"
    )
    abstract_text: Optional[str] = Field(
        "",
        description="Deskripsi layer group"
    )
    client_user_id: Optional[Any] = Field(
        None,
        description="ID user eksternal klien"
    )
    layer_ids: List[Any] = Field(
        ...,
        description="Daftar ID layer yang digabungkan ke dalam group (terurut)"
    )


class S2SUpdateLayerGroupRequest(BaseModel):
    model_config = {"extra": "allow"}

    name: Optional[str] = Field(None, description="Nama teknis layer group")
    title: Optional[str] = Field(None, description="Judul display layer group")
    abstract_text: Optional[str] = Field(None, description="Deskripsi layer group")
    client_user_id: Optional[Any] = Field(None, description="ID user eksternal klien")
    layer_ids: Optional[List[Any]] = Field(None, description="Daftar ID layer yang digabungkan ke dalam group (terurut)")


# ── Helper Function: Apply Style to Layer ──────────────────────────────────────


def _apply_layer_style(
    workspace_name: str,
    store_name: str,
    layer_name: str,
    style_sld: Optional[str],
    style_entries: Optional[List[Dict[str, Any]]],
    legends: Optional[Dict[str, Any]] = None,
):
    style_name = f"style_{store_name}"
    
    # 1. Coba terapkan XML SLD siap pakai jika ada
    if style_sld and style_sld.strip():
        try:
            # Sanitasi jika ada atribut yang menempel tanpa spasi seperti quantity="1"label="..."
            cleaned_sld = re.sub(r'("[0-9a-zA-Z#_.-]+")(label|quantity|opacity|color)=', r'\1 \2=', style_sld.strip())
            
            # Sanitasi karakter khusus XML (<, >, &) di dalam nilai atribut label="..." agar GeoServer tidak melempar SAXParseException
            def escape_label_attr(match):
                prefix = match.group(1)
                val = match.group(2)
                val = val.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                suffix = match.group(3)
                return f'{prefix}{val}{suffix}'

            cleaned_sld = re.sub(r'(label=")(.*?)(")', escape_label_attr, cleaned_sld)

            apply_sld_to_layer(
                workspace=workspace_name,
                layer_name=store_name,
                style_name=style_name,
                sld_xml=cleaned_sld,
            )
            print(f"[S2S] XML SLD '{style_name}' berhasil diterapkan ke layer '{layer_name}'")
            return
        except Exception as se:
            print(f"[S2S] Peringatan: XML SLD gagal diterapkan: {se}. Mencoba fallback ke style entries / legends.")

    # 2. Coba terapkan dari array style_entries jika ada
    if style_entries and len(style_entries) > 0:
        try:
            generated_xml = generate_raster_sld(
                style_name=style_name,
                color_entries=style_entries,
                style_type="ramp",
            )
            apply_sld_to_layer(
                workspace=workspace_name,
                layer_name=store_name,
                style_name=style_name,
                sld_xml=generated_xml,
            )
            print(f"[S2S] Generated SLD dari style_entries '{style_name}' berhasil diterapkan ke '{layer_name}'")
            return
        except Exception as se2:
            print(f"[S2S] Peringatan: Generated SLD style_entries gagal: {se2}")

    # 3. Fallback: Ekstrak aturan warna dari metadata legends
    if legends and isinstance(legends, dict):
        try:
            target_legend = (
                legends.get("FloodRisk")
                or legends.get("FloodEvent")
                or legends.get("Rainfall")
                or next((v for v in legends.values() if isinstance(v, dict) and "items" in v), None)
            )
            if target_legend and isinstance(target_legend, dict) and "items" in target_legend:
                extracted_entries = []
                is_flood_risk = "flood" in layer_name.lower() and "risk" in layer_name.lower()
                style_type = "values" if is_flood_risk else "ramp"

                for idx, itm in enumerate(target_legend["items"]):
                    extracted_entries.append({
                        "quantity": idx + 1,
                        "color": itm.get("color", "#008000"),
                        "opacity": 1.0,
                        "label": itm.get("label", f"Class {idx + 1}").replace("<", "&lt;").replace(">", "&gt;")
                    })
                
                generated_xml = generate_raster_sld(
                    style_name=style_name,
                    color_entries=extracted_entries,
                    style_type=style_type,
                )
                apply_sld_to_layer(
                    workspace=workspace_name,
                    layer_name=store_name,
                    style_name=style_name,
                    sld_xml=generated_xml,
                )
                print(f"[S2S] Fallback SLD dari legends ({style_type}) berhasil diterapkan ke layer '{layer_name}'")
                return
        except Exception as se3:
            print(f"[S2S] Peringatan: Fallback SLD dari legends gagal: {se3}")

    # 4. Fallback Terakhir: Default Grayscale
    try:
        default_style_name = f"default_{workspace_name}"
        if not style_exists_in_geoserver(default_style_name):
            generic_sld = generate_raster_sld(
                style_name=default_style_name,
                color_entries=[
                    {"quantity": 0,   "color": "#000000", "opacity": 1.0, "label": "Low"},
                    {"quantity": 128, "color": "#7f7f7f", "opacity": 1.0, "label": "Mid"},
                    {"quantity": 255, "color": "#ffffff", "opacity": 1.0, "label": "High"},
                ],
                style_type="ramp",
            )
            apply_sld_to_layer(
                workspace=workspace_name,
                layer_name=store_name,
                style_name=default_style_name,
                sld_xml=generic_sld,
            )
        else:
            assign_style_to_layer(workspace_name, store_name, default_style_name)
    except Exception as se_last:
        print(f"[S2S] Gagal menerapkan style default GeoServer: {se_last}")


# ── 1. POST /s2s/publish-from-url (Format FlowGIS & Eksternal) ─────────────────

@router.post("/publish-from-url", status_code=status.HTTP_201_CREATED)
async def s2s_publish_from_url(
    req: S2SPublishFromUrlRequest,
    request: Request,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    ## Publish GeoTIFF Langsung dari URL (Format FlowGIS / Google Earth Engine)

    Menerima URL download GeoTIFF dan dokumen XML SLD siap pakai.
    Backend Management Spatial akan mengunduh file secara server-to-server,
    mempublikasikan ke GeoServer, menerapkan SLD, dan menyimpan relasi user eksternal.
    """
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    file_path = None

    try:
        # 1. Validasi Workspace
        actual_ws_id = int(req.workspace_id) if str(req.workspace_id).isdigit() else decode_id(req.workspace_id)
        if actual_ws_id is None:
            raise HTTPException(status_code=400, detail="ID Workspace tidak valid.")

        workspace = (
            db.query(Workspace)
            .join(Project)
            .filter(
                Workspace.id == actual_ws_id,
                Project.id == api_key.project_id,
            )
            .first()
        )
        if workspace is None:
            raise HTTPException(
                status_code=403,
                detail="Workspace tidak ditemukan atau API Key tidak memiliki akses ke workspace ini."
            )

        # 2. Unduh GeoTIFF dari URL
        store_name = f"s2s_{uuid.uuid4().hex}"
        file_path = os.path.normpath(os.path.join(RASTER_PATH, f"{store_name}.tif"))

        try:
            async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
                dl_resp = await client.get(req.download_url)
                if dl_resp.status_code != 200:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Gagal mengunduh file dari download_url. Status HTTP: {dl_resp.status_code}"
                    )
                with open(file_path, "wb") as f:
                    f.write(dl_resp.content)
        except httpx.RequestError as re:
            raise HTTPException(
                status_code=400,
                detail=f"Koneksi ke download_url gagal: {str(re)}"
            )

        # 3. Validasi Single Band GeoTIFF
        try:
            validate_single_band(file_path)
        except ValueError as ve:
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(status_code=400, detail=str(ve))

        # 4. Ambil Metadata Raster
        tiff_metadata = get_tiff_metadata(file_path)
        epsg = tiff_metadata["epsg"]
        bounds = tiff_metadata["bbox"]
        width = tiff_metadata["dimensions"]["width"]
        height = tiff_metadata["dimensions"]["height"]

        geom = box(bounds.left, bounds.bottom, bounds.right, bounds.top)

        # 5. Struktur Metadata JSON
        user_meta = req.metadata if req.metadata is not None else (req.extra_metadata or {})
        metadata_json = {
            "download_url": req.download_url,
            "statistics": req.statistics or user_meta.get("statistics"),
            "legends": req.legends or user_meta.get("legends"),
            "maps": req.maps or user_meta.get("maps"),
            "extra": user_meta,
            "source_system": "FlowGIS" if "earthengine" in req.download_url else "External_S2S",
        }

        # 6. Simpan Layer ke Database
        client_uid = str(req.client_user_id) if req.client_user_id is not None else None
        layer_meta = Layer(
            workspace_id=workspace.id,
            name=req.layer_name,
            description=req.description or "",
            geoserver_name=store_name,
            epsg=epsg,
            bbox=from_shape(geom, srid=epsg),
            width=width,
            height=height,
            layer_type="raster",
            data_type="GeoTiff",
            file_path=file_path,
            status="PUBLISHED",
            client_user_id=client_uid,
            metadata_json=metadata_json,
        )
        db.add(layer_meta)
        db.commit()


        # 7. Publish ke GeoServer
        publish_ok = geo.create_coveragestore(
            layer_name=store_name,
            path=file_path,
            workspace=workspace.ws_name,
        )
        if not publish_ok:
            db.delete(layer_meta)
            db.commit()
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(
                status_code=500,
                detail="Gagal mempublikasikan layer ke GeoServer."
            )

        # 8. Terapkan Style SLD
        req_legends = req.legends or (req.metadata.get('legends') if isinstance(req.metadata, dict) else None)
        _apply_layer_style(
            workspace_name=workspace.ws_name,
            store_name=store_name,
            layer_name=req.layer_name,
            style_sld=req.style_sld,
            style_entries=req.style,
            legends=req_legends,
        )

        # 9. Catat Log Aktivitas
        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_PUBLISH_URL",
            resource_type="LAYER",
            resource_id=str(layer_meta.id),
            resource_name=req.layer_name,
            status="SUCCESS",
            api_key_id=api_key.id,
            project_id=api_key.project_id,
            client_user_id=req.client_user_id,
            client_user_email=req.client_user_email,
            client_user_name=req.client_user_name,
            ip_address=client_ip,
            user_agent=user_agent,
            meta_data={
                "workspace_id": workspace.id,
                "workspace_name": workspace.name,
                "download_url": req.download_url,
                "has_style_sld": bool(req.style_sld),
            }
        )

        wms_base = (os.getenv("GEOSERVER_WMS_URL") or "http://localhost:8080/geoserver").rstrip("/")
        wms_url = f"{wms_base}/{workspace.ws_name}/wms"

        return {
            "success": True,
            "detail": f"Layer '{req.layer_name}' berhasil dipublikasikan dari URL via S2S.",
            "data": {
                "id": layer_meta.id,
                "layer_name": req.layer_name,
                "geoserver_name": store_name,
                "workspace": workspace.ws_name,
                "workspace_display_name": workspace.name,
                "client_user_id": req.client_user_id,
                "wms_url": wms_url,
                "wms_layers_param": f"{workspace.ws_name}:{store_name}",
                "epsg": epsg,
                "bbox": [bounds.left, bounds.bottom, bounds.right, bounds.top],
                "width": width,
                "height": height,
                "metadata": metadata_json,
                "created_at": layer_meta.created_at,
            }
        }

    except HTTPException as he:
        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_PUBLISH_URL",
            resource_type="LAYER",
            resource_name=req.layer_name,
            status="FAILED",
            api_key_id=api_key.id,
            project_id=api_key.project_id,
            client_user_id=req.client_user_id,
            ip_address=client_ip,
            user_agent=user_agent,
            meta_data={"error": he.detail}
        )
        raise
    except Exception as e:
        db.rollback()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_PUBLISH_URL",
            resource_type="LAYER",
            resource_name=req.layer_name,
            status="FAILED",
            api_key_id=api_key.id,
            project_id=api_key.project_id,
            client_user_id=req.client_user_id,
            ip_address=client_ip,
            user_agent=user_agent,
            meta_data={"error": str(e)}
        )
        raise HTTPException(status_code=500, detail=f"Terjadi kesalahan internal: {str(e)}")


# ── 2. POST /s2s/publish (Format Multipart File Upload) ────────────────────────

@router.post("/publish", status_code=status.HTTP_201_CREATED)
async def s2s_publish_layer(
    workspace_id: str = Form(..., description="Hashed ID atau ID integer workspace tujuan"),
    layer_name: str = Form(..., description="Nama tampilan layer"),
    description: Optional[str] = Form("", description="Deskripsi layer"),
    file: UploadFile = File(..., description="File GeoTIFF (.tif/.tiff) single-band"),
    style: Optional[str] = Form(None, description="JSON array ColorEntry"),
    style_sld: Optional[str] = Form(None, description="XML SLD utuh (opsional)"),
    client_user_id: Optional[str] = Form(None, description="ID user klien (contoh: ID pengguna Laravel)"),
    client_user_email: Optional[str] = Form(None, description="Email user klien"),
    client_user_name: Optional[str] = Form(None, description="Nama user klien"),
    metadata_json: Optional[str] = Form(None, description="JSON string data tambahan (statistics, legends)"),
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    ## Publish GeoTIFF Layer via File Upload (Multipart Form Data)
    """
    client_ip = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent") if request else None
    file_path = None

    try:
        actual_ws_id = int(workspace_id) if str(workspace_id).isdigit() else decode_id(workspace_id)
        if actual_ws_id is None:
            raise HTTPException(status_code=400, detail="ID Workspace tidak valid.")

        workspace = (
            db.query(Workspace)
            .join(Project)
            .filter(Workspace.id == actual_ws_id, Project.id == api_key.project_id)
            .first()
        )
        if workspace is None:
            raise HTTPException(status_code=403, detail="Workspace tidak ditemukan atau API Key tidak memiliki akses.")

        file_extension = os.path.splitext(file.filename or "")[1].lower()
        if file_extension not in ('.tif', '.tiff'):
            raise HTTPException(status_code=400, detail="Hanya file GeoTIFF (.tif / .tiff) yang didukung.")

        store_name = f"s2s_{uuid.uuid4().hex}"
        unique_filename = f"{store_name}{file_extension}"
        file_path = os.path.normpath(os.path.join(RASTER_PATH, unique_filename))

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            validate_single_band(file_path)
        except ValueError as ve:
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(status_code=400, detail=str(ve))

        tiff_metadata = get_tiff_metadata(file_path)
        epsg = tiff_metadata["epsg"]
        bounds = tiff_metadata["bbox"]
        width = tiff_metadata["dimensions"]["width"]
        height = tiff_metadata["dimensions"]["height"]

        geom = box(bounds.left, bounds.bottom, bounds.right, bounds.top)

        # Parse extra metadata jika ada
        parsed_meta = None
        if metadata_json:
            try:
                parsed_meta = json.loads(metadata_json)
            except Exception:
                parsed_meta = {"raw": metadata_json}

        layer_meta = Layer(
            workspace_id=workspace.id,
            name=layer_name,
            description=description,
            geoserver_name=store_name,
            epsg=epsg,
            bbox=from_shape(geom, srid=epsg),
            width=width,
            height=height,
            layer_type="raster",
            data_type="GeoTiff",
            file_path=file_path,
            status="PUBLISHED",
            client_user_id=client_user_id,
            metadata_json=parsed_meta,
        )
        db.add(layer_meta)
        db.commit()

        publish_ok = geo.create_coveragestore(
            layer_name=store_name,
            path=file_path,
            workspace=workspace.ws_name,
        )
        if not publish_ok:
            db.delete(layer_meta)
            db.commit()
            if os.path.exists(file_path):
                os.remove(file_path)
            raise HTTPException(status_code=500, detail="Gagal mempublikasikan layer ke GeoServer.")

        # Parse style entries jika diberikan
        style_entries = None
        if style:
            try:
                style_entries = json.loads(style)
            except Exception:
                pass

        _apply_layer_style(
            workspace_name=workspace.ws_name,
            store_name=store_name,
            layer_name=layer_name,
            style_sld=style_sld,
            style_entries=style_entries,
            legends=(parsed_meta.get('legends') if isinstance(parsed_meta, dict) else None),
        )

        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_PUBLISH_FILE",
            resource_type="LAYER",
            resource_id=str(layer_meta.id),
            resource_name=layer_name,
            status="SUCCESS",
            api_key_id=api_key.id,
            project_id=api_key.project_id,
            client_user_id=client_user_id,
            client_user_email=client_user_email,
            client_user_name=client_user_name,
            ip_address=client_ip,
            user_agent=user_agent,
            meta_data={"workspace_id": workspace.id, "file_name": file.filename}
        )

        wms_base = (os.getenv("GEOSERVER_WMS_URL") or "http://localhost:8080/geoserver").rstrip("/")
        wms_url = f"{wms_base}/{workspace.ws_name}/wms"

        return {
            "success": True,
            "detail": f"Layer '{layer_name}' berhasil dipublikasikan via S2S.",
            "data": {
                "id": layer_meta.id,
                "layer_name": layer_name,
                "geoserver_name": store_name,
                "workspace": workspace.ws_name,
                "client_user_id": client_user_id,
                "wms_url": wms_url,
                "wms_layers_param": f"{workspace.ws_name}:{store_name}",
                "epsg": epsg,
                "bbox": [bounds.left, bounds.bottom, bounds.right, bounds.top],
                "width": width,
                "height": height,
                "metadata": parsed_meta,
                "created_at": layer_meta.created_at,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Terjadi kesalahan internal: {e}")


# ── 3. GET /s2s/layers (Daftar Layer Pengguna untuk Multi-User Client) ─────────

@router.get("/layers")
def s2s_get_layers(
    client_user_id: Optional[str] = Query(None, description="Filter berdasarkan ID pengguna di sistem klien (Laravel FlowGIS)"),
    workspace_id: Optional[str] = Query(None, description="Filter berdasarkan workspace tertentu"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    ## Ambil Daftar Layer untuk Klien Eksternal / Multi-User

    Digunakan oleh sistem seperti Laravel (`flowgis-business-process`) untuk
    mengambil riwayat hasil analisis layer yang dimiliki oleh pengguna tertentu.
    """
    client_ip = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent") if request else None

    try:
        wms_base = (os.getenv("GEOSERVER_WMS_URL") or "http://localhost:8080/geoserver").rstrip("/")

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
            .filter(Project.id == api_key.project_id)
        )

        # Filter workspace
        if workspace_id:
            actual_ws_id = int(workspace_id) if str(workspace_id).isdigit() else decode_id(workspace_id)
            if actual_ws_id is not None:
                base_query = base_query.filter(Workspace.id == actual_ws_id)

        # Filter multi-user klien
        if client_user_id:
            base_query = base_query.filter(Layer.client_user_id == str(client_user_id))

        total_count = base_query.count()
        offset = (page - 1) * size

        layers_result = (
            base_query
            .order_by(Layer.created_at.desc())
            .offset(offset)
            .limit(size)
            .all()
        )

        items = [
            {
                "id": layer.id,
                "layer_name": layer.name,
                "description": layer.description,
                "workspace_id": layer.workspace_id,
                "workspace_name": ws_name,
                "workspace_display_name": ws_display,
                "client_user_id": layer.client_user_id,
                "data_type": layer.data_type,
                "epsg": layer.epsg,
                "bbox": [minx, miny, maxx, maxy] if minx is not None else None,
                "wms_url": f"{wms_base}/{ws_name}/wms",
                "wms_layers_param": f"{ws_name}:{layer.geoserver_name}",
                "metadata": layer.metadata_json,
                "created_at": layer.created_at,
            }
            for layer, ws_name, ws_display, minx, miny, maxx, maxy in layers_result
        ]

        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_LIST_LAYERS",
            resource_type="LAYER",
            status="SUCCESS",
            api_key_id=api_key.id,
            project_id=api_key.project_id,
            client_user_id=client_user_id,
            ip_address=client_ip,
            user_agent=user_agent,
            meta_data={"total_found": total_count, "client_user_id_filter": client_user_id}
        )

        return {
            "success": True,
            "data": items,
            "pagination": {
                "total": total_count,
                "page": page,
                "size": size,
                "total_pages": (total_count + size - 1) // size if size else 0,
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil daftar layer: {str(e)}")


# ── 3B. DELETE /s2s/layers/{layer_id} (Hapus Layer via S2S) ───────────────────

@router.delete("/layers/{layer_id}")
async def s2s_delete_layer(
    layer_id: str,
    client_user_id: Optional[str] = Query(None, description="ID user eksternal klien"),
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Menghapus layer dari GeoServer, PostGIS, dan disk via S2S.
    """
    client_ip = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent") if request else None

    try:
        actual_id = int(layer_id) if str(layer_id).isdigit() else decode_id(layer_id)
        if actual_id is None:
            raise HTTPException(status_code=400, detail="ID Layer tidak valid.")

        query = (
            db.query(Layer)
            .join(Workspace, Workspace.id == Layer.workspace_id)
            .filter(Layer.id == actual_id, Workspace.project_id == api_key.project_id)
        )
        if client_user_id:
            query = query.filter(Layer.client_user_id == str(client_user_id))

        layer = query.first()
        if not layer:
            raise HTTPException(status_code=404, detail="Layer tidak ditemukan atau tidak memiliki hak akses.")

        workspace = db.query(Workspace).filter(Workspace.id == layer.workspace_id).first()

        # 1. Hapus dari GeoServer
        if workspace and layer.geoserver_name:
            try:
                geo.delete_coveragestore(coveragestore_name=layer.geoserver_name, workspace=workspace.ws_name)
            except Exception as ge:
                print(f"[S2S] Peringatan: Gagal menghapus coverage store GeoServer: {ge}")
            try:
                geo.delete_style(style_name=f"style_{layer.geoserver_name}")
            except Exception:
                pass

        # 2. Hapus file fisik jika ada
        if layer.file_path and os.path.exists(layer.file_path):
            try:
                os.remove(layer.file_path)
            except Exception as fe:
                print(f"[S2S] Peringatan: Gagal menghapus file raster fisik: {fe}")

        layer_name = layer.name
        db.delete(layer)
        db.commit()

        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_DELETE_LAYER",
            resource_type="LAYER",
            resource_id=str(actual_id),
            resource_name=layer_name,
            status="SUCCESS",
            api_key_id=api_key.id,
            project_id=api_key.project_id,
            client_user_id=client_user_id,
            ip_address=client_ip,
            user_agent=user_agent,
            meta_data={"deleted_layer_id": actual_id, "layer_name": layer_name}
        )

        return {"success": True, "detail": f"Layer '{layer_name}' (ID: {actual_id}) berhasil dihapus."}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal menghapus layer: {str(e)}")


# ── 4. POST /s2s/layer-groups (Create Layer Group via S2S) ───────────────────

@router.post("/layer-groups", status_code=status.HTTP_201_CREATED)
async def s2s_create_layer_group(
    req: S2SCreateLayerGroupRequest,
    request: Request,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Membuat Layer Group baru di GeoServer via S2S, dihubungkan dengan client_user_id.
    """
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    try:
        actual_ws_id = int(req.workspace_id) if str(req.workspace_id).isdigit() else decode_id(str(req.workspace_id))
        if actual_ws_id is None:
            raise HTTPException(status_code=400, detail="Invalid Workspace ID.")

        workspace = (
            db.query(Workspace)
            .join(Project, Project.id == Workspace.project_id)
            .filter(Workspace.id == actual_ws_id, Project.id == api_key.project_id)
            .first()
        )
        if not workspace:
            raise HTTPException(status_code=403, detail="Workspace not found or access denied.")

        # Sanitasi layer_ids
        raw_ids = [int(lid) if str(lid).isdigit() else decode_id(str(lid)) for lid in req.layer_ids]
        valid_ids = [i for i in raw_ids if i is not None]

        layers = (
            db.query(Layer)
            .filter(Layer.id.in_(valid_ids), Layer.workspace_id == actual_ws_id)
            .all()
        )
        if not layers:
            raise HTTPException(status_code=400, detail="No valid layers found to merge.")

        layer_dict = {l.id: l for l in layers}
        ordered_layers = [layer_dict[lid] for lid in valid_ids if lid in layer_dict]

        # Di GeoServer WMS, layer yang dirender paling akhir adalah yang tampil paling atas
        geoserver_layer_names = [l.geoserver_name for l in reversed(ordered_layers)]

        # Unique technical name
        tech_name = f"grp_{uuid.uuid4().hex[:10]}"
        group_title = req.title or req.name

        create_geoserver_layergroup(
            name=tech_name,
            title=group_title,
            workspace=workspace.ws_name,
            layers=geoserver_layer_names,
            mode="single",
            abstract_text=req.abstract_text or "",
            keywords=["s2s", "flowgis"]
        )

        client_uid = str(req.client_user_id) if req.client_user_id is not None else None
        layer_group = LayerGroup(
            workspace_id=actual_ws_id,
            name=tech_name,
            title=group_title,
            abstract_text=req.abstract_text or "",
            mode="single",
            client_user_id=client_uid,
            keywords=["s2s", "flowgis"]
        )
        db.add(layer_group)
        db.commit()
        db.refresh(layer_group)

        # Simpan anggota group
        for order_idx, lyr in enumerate(ordered_layers):
            db.add(LayerGroupLayer(
                layer_group_id=layer_group.id,
                layer_id=lyr.id,
                layer_order=order_idx
            ))
        db.commit()

        # Bounding box layer teratas (index 0)
        top_layer = ordered_layers[0] if ordered_layers else None
        top_bbox = None
        if top_layer and top_layer.bbox is not None:
            bbox_res = db.query(
                func.ST_XMin(top_layer.bbox),
                func.ST_YMin(top_layer.bbox),
                func.ST_XMax(top_layer.bbox),
                func.ST_YMax(top_layer.bbox)
            ).first()
            if bbox_res and bbox_res[0] is not None:
                top_bbox = [bbox_res[0], bbox_res[1], bbox_res[2], bbox_res[3]]

        wms_base = (os.getenv("GEOSERVER_WMS_URL") or "http://localhost:8080/geoserver").rstrip("/")
        wms_url = f"{wms_base}/{workspace.ws_name}/wms"

        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_CREATE_LAYER_GROUP",
            resource_type="LAYER_GROUP",
            resource_id=str(layer_group.id),
            resource_name=group_title,
            status="SUCCESS",
            api_key_id=api_key.id,
            project_id=api_key.project_id,
            client_user_id=client_uid,
            ip_address=client_ip,
            user_agent=user_agent
        )

        return {
            "success": True,
            "data": {
                "id": layer_group.id,
                "name": layer_group.name,
                "title": layer_group.title,
                "workspace_id": workspace.id,
                "workspace_name": workspace.ws_name,
                "client_user_id": client_uid,
                "wms_url": wms_url,
                "wms_layers_param": f"{workspace.ws_name}:{tech_name}",
                "bbox": top_bbox,
                "layers_count": len(ordered_layers),
                "created_at": layer_group.created_at
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal membuat layer group: {str(e)}")


# ── 5. GET /s2s/layer-groups (List Layer Groups via S2S) ───────────────────────

@router.get("/layer-groups")
async def s2s_list_layer_groups(
    client_user_id: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    try:
        wms_base = (os.getenv("GEOSERVER_WMS_URL") or "http://localhost:8080/geoserver").rstrip("/")

        query = (
            db.query(LayerGroup, Workspace.ws_name, Workspace.name.label("ws_display"))
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(Project.id == api_key.project_id)
        )

        if workspace_id:
            actual_ws_id = int(workspace_id) if str(workspace_id).isdigit() else decode_id(str(workspace_id))
            if actual_ws_id is not None:
                query = query.filter(Workspace.id == actual_ws_id)

        if client_user_id:
            query = query.filter(LayerGroup.client_user_id == str(client_user_id))

        groups = query.order_by(LayerGroup.created_at.desc()).all()

        results = []
        for grp, ws_name, ws_display in groups:
            # Cari layer teratas untuk bbox
            first_lgl = (
                db.query(LayerGroupLayer)
                .filter(LayerGroupLayer.layer_group_id == grp.id)
                .order_by(LayerGroupLayer.layer_order.asc())
                .first()
            )
            bbox = None
            if first_lgl and first_lgl.layer and first_lgl.layer.bbox is not None:
                b = db.query(
                    func.ST_XMin(first_lgl.layer.bbox),
                    func.ST_YMin(first_lgl.layer.bbox),
                    func.ST_XMax(first_lgl.layer.bbox),
                    func.ST_YMax(first_lgl.layer.bbox)
                ).first()
                if b and b[0] is not None:
                    bbox = [b[0], b[1], b[2], b[3]]

            # Ambil anggota layer
            member_layers = (
                db.query(Layer.id, Layer.name, Layer.geoserver_name, LayerGroupLayer.layer_order)
                .join(LayerGroupLayer, LayerGroupLayer.layer_id == Layer.id)
                .filter(LayerGroupLayer.layer_group_id == grp.id)
                .order_by(LayerGroupLayer.layer_order.asc())
                .all()
            )

            results.append({
                "id": grp.id,
                "name": grp.name,
                "title": grp.title,
                "abstract_text": grp.abstract_text,
                "workspace_id": grp.workspace_id,
                "workspace_name": ws_name,
                "workspace_display_name": ws_display,
                "client_user_id": grp.client_user_id,
                "wms_url": f"{wms_base}/{ws_name}/wms",
                "wms_layers_param": f"{ws_name}:{grp.name}",
                "bbox": bbox,
                "layers": [
                    {
                        "id": ml[0],
                        "name": ml[1],
                        "order": ml[3]
                    }
                    for ml in member_layers
                ],
                "created_at": grp.created_at
            })

        return {
            "success": True,
            "data": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve list of layer groups: {str(e)}")


# ── 6. DELETE /s2s/layer-groups/{id} ──────────────────────────────────────────

@router.delete("/layer-groups/{id}")
async def s2s_delete_layer_group(
    id: int,
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    try:
        grp = (
            db.query(LayerGroup)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(LayerGroup.id == id, Project.id == api_key.project_id)
            .first()
        )
        if not grp:
            raise HTTPException(status_code=404, detail="Layer Group not found.")

        workspace = db.query(Workspace).filter(Workspace.id == grp.workspace_id).first()
        if workspace:
            delete_geoserver_layergroup(grp.name, workspace=workspace.ws_name)

        db.delete(grp)
        db.commit()

        return {"success": True, "detail": f"Layer Group #{id} deleted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete layer group: {str(e)}")


# ── 7. PUT /s2s/layer-groups/{id} (Update Layer Group via S2S) ─────────────────

@router.put("/layer-groups/{id}")
async def s2s_update_layer_group(
    id: int,
    req: S2SUpdateLayerGroupRequest,
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Update Layer Group (title, description, or member layer order) via S2S.
    """
    client_ip = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent") if request else None

    try:
        query = (
            db.query(LayerGroup)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(LayerGroup.id == id, Project.id == api_key.project_id)
        )
        if req.client_user_id:
            query = query.filter(LayerGroup.client_user_id == str(req.client_user_id))

        grp = query.first()
        if not grp:
            raise HTTPException(status_code=404, detail="Layer Group not found.")

        workspace = db.query(Workspace).filter(Workspace.id == grp.workspace_id).first()
        if not workspace:
            raise HTTPException(status_code=400, detail="Workspace not found.")

        if req.title is not None:
            grp.title = req.title
        if req.abstract_text is not None:
            grp.abstract_text = req.abstract_text

        # Jika ada pembaruan daftar layer anggota
        if req.layer_ids is not None:
            if len(req.layer_ids) == 0:
                raise HTTPException(status_code=400, detail="Layer Group must have at least 1 member layer.")

            # Cari layer anggota di workspace ini
            layers = (
                db.query(Layer)
                .filter(Layer.id.in_(req.layer_ids), Layer.workspace_id == workspace.id)
                .all()
            )
            layer_dict = {l.id: l for l in layers}
            valid_layers = [layer_dict[lid] for lid in req.layer_ids if lid in layer_dict]

            if len(valid_layers) == 0:
                raise HTTPException(status_code=400, detail="No valid member layers found.")

            # Hapus group lama di GeoServer & buat ulang dengan susunan layer baru
            delete_geoserver_layergroup(grp.name, workspace=workspace.ws_name)
            geoserver_layer_names = [l.geoserver_name for l in reversed(valid_layers)]
            create_geoserver_layergroup(
                name=grp.name,
                title=grp.title or grp.name,
                workspace=workspace.ws_name,
                layers=geoserver_layer_names,
                mode="single",
                abstract_text=grp.abstract_text or "",
            )

            # Perbarui relasi di database
            db.query(LayerGroupLayer).filter(LayerGroupLayer.layer_group_id == grp.id).delete()
            for idx, lyr in enumerate(valid_layers):
                lgl = LayerGroupLayer(
                    layer_group_id=grp.id,
                    layer_id=lyr.id,
                    layer_order=idx
                )
                db.add(lgl)

        db.commit()

        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_UPDATE_LAYER_GROUP",
            resource_type="LAYER_GROUP",
            resource_id=str(grp.id),
            resource_name=grp.name,
            status="SUCCESS",
            api_key_id=api_key.id,
            project_id=api_key.project_id,
            client_user_id=grp.client_user_id,
            ip_address=client_ip,
            user_agent=user_agent,
            meta_data={"layer_group_id": grp.id, "title": grp.title, "layer_count": len(req.layer_ids) if req.layer_ids else None}
        )

        wms_base = (os.getenv("GEOSERVER_WMS_URL") or "http://localhost:8080/geoserver").rstrip("/")
        bbox_val = None
        first_lgl = (
            db.query(LayerGroupLayer)
            .filter(LayerGroupLayer.layer_group_id == grp.id)
            .order_by(LayerGroupLayer.layer_order.asc())
            .first()
        )
        if first_lgl and first_lgl.layer and first_lgl.layer.bbox is not None:
            b = db.query(
                func.ST_XMin(first_lgl.layer.bbox),
                func.ST_YMin(first_lgl.layer.bbox),
                func.ST_XMax(first_lgl.layer.bbox),
                func.ST_YMax(first_lgl.layer.bbox)
            ).first()
            if b and b[0] is not None:
                bbox_val = [b[0], b[1], b[2], b[3]]

        return {
            "success": True,
            "detail": f"Layer Group '{grp.title or grp.name}' updated successfully.",
            "data": {
                "id": grp.id,
                "name": grp.name,
                "title": grp.title,
                "abstract_text": grp.abstract_text,
                "workspace_id": grp.workspace_id,
                "workspace_name": workspace.ws_name,
                "client_user_id": grp.client_user_id,
                "wms_url": f"{wms_base}/{workspace.ws_name}/wms",
                "wms_layers_param": f"{workspace.ws_name}:{grp.name}",
                "bbox": bbox_val,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal memperbarui layer group: {str(e)}")


