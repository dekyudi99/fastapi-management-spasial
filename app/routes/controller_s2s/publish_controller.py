from fastapi import APIRouter, HTTPException, Depends, UploadFile, Form, File, Request, status
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
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

from services.vector_service import (
    read_vector_file_to_gdf,
    simplify_vector_gdf,
    publish_vector_to_geoserver_postgis,
)

router = APIRouter()
geo = get_geoserver_connection()

RASTER_PATH = "/data_raster"
VECTOR_PATH = "/data_vector"
os.makedirs(RASTER_PATH, exist_ok=True)
os.makedirs(VECTOR_PATH, exist_ok=True)
VECTOR_FORMATS = ('.shp', '.zip', '.geojson', '.json', '.gpkg', '.csv')


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


# ── 1. POST /publish-from-url (Format FlowGIS & Eksternal) ─────────────────

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

        wms_base = (os.getenv("GEOSERVER_WMS_URL")).rstrip("/")
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


# ── 2. POST /publish (Format Multipart File Upload) ────────────────────────

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

        wms_base = (os.getenv("GEOSERVER_WMS_URL")).rstrip("/")
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


# ── 3. POST /publish-vector (S2S Vector Upload via File Upload) ────────────────

@router.post("/publish-vector", status_code=status.HTTP_201_CREATED)
async def s2s_publish_vector_layer(
    workspace_id: str = Form(..., description="Hashed ID, integer ID, atau nama workspace tujuan"),
    layer_name: str = Form(..., description="Nama tampilan layer"),
    description: Optional[str] = Form("", description="Deskripsi layer"),
    file: UploadFile = File(..., description="File spasial vektor (.zip Shapefile, .geojson, .gpkg, .csv, .shp)"),
    client_user_id: Optional[str] = Form(None, description="ID user klien (contoh: ID pengguna Laravel)"),
    client_user_email: Optional[str] = Form(None, description="Email user klien"),
    client_user_name: Optional[str] = Form(None, description="Nama user klien"),
    metadata_json: Optional[str] = Form(None, description="JSON string metadata kustom"),
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    ## Publish Vector Layer via S2S File Upload
    Mengunggah berkas vektor (Shapefile zip, GeoJSON, GPKG, CSV), melakukan simplifikasi
    dengan nilai toleransi tetap dari environment (SIMPLIFY_TOLERANCE), menyimpan ke PostGIS,
    dan menerbitkan FeatureType WMS ke GeoServer.
    """
    client_ip = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent") if request else None
    file_path = None

    try:
        # Cari workspace berdasarkan integer ID, decode hash ID, atau nama workspace
        workspace = None
        if str(workspace_id).isdigit():
            workspace = (
                db.query(Workspace)
                .join(Project)
                .filter(Workspace.id == int(workspace_id), Project.id == api_key.project_id)
                .first()
            )
        else:
            actual_ws_id = decode_id(workspace_id)
            if actual_ws_id is not None:
                workspace = (
                    db.query(Workspace)
                    .join(Project)
                    .filter(Workspace.id == actual_ws_id, Project.id == api_key.project_id)
                    .first()
                )
            if not workspace:
                # Coba cari berdasarkan nama workspace (contoh: "geosocial")
                workspace = (
                    db.query(Workspace)
                    .filter(Workspace.ws_name == str(workspace_id).strip())
                    .first()
                )

        if workspace is None:
            raise HTTPException(status_code=403, detail="Workspace tidak ditemukan atau API Key tidak memiliki akses.")

        file_extension = os.path.splitext(file.filename or "")[1].lower()
        if file_extension not in VECTOR_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"Format berkas '{file_extension}' tidak didukung! Format yang didukung: {', '.join(VECTOR_FORMATS)}"
            )

        clean_slug = re.sub(r'[^a-zA-Z0-9_]', '_', layer_name.lower()).strip('_')[:20]
        store_name = f"s2s_vec_{clean_slug}_{uuid.uuid4().hex[:8]}"
        unique_filename = f"{store_name}{file_extension}"
        file_path = os.path.normpath(os.path.join(VECTOR_PATH, unique_filename))

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Baca file ke GeoPandas GeoDataFrame
        gdf, format_name = read_vector_file_to_gdf(file_path)

        # Toleransi simplifikasi diambil dari environment secara tetap (user tidak bisa mengubah)
        env_tolerance = os.getenv("SIMPLIFY_TOLERANCE")
        if not env_tolerance:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Variabel environment SIMPLIFY_TOLERANCE belum disetel."
            )
        simplify_tolerance = float(env_tolerance)

        gdf, stats = simplify_vector_gdf(gdf, tolerance=simplify_tolerance, preserve_topology=True)

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
        table_name = f"vec_{store_name}"
        publish_vector_to_geoserver_postgis(
            gdf=gdf,
            table_name=table_name,
            workspace_name=workspace.ws_name,
            title=layer_name,
            geom_type=dominant_geom
        )

        # Parse extra metadata jika ada
        parsed_meta = None
        if metadata_json:
            try:
                parsed_meta = json.loads(metadata_json)
            except Exception:
                parsed_meta = {"raw": metadata_json}

        full_metadata = {
            "simplification": stats,
            "extra": parsed_meta
        }

        layer_meta = Layer(
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
            metadata_json=full_metadata
        )

        db.add(layer_meta)
        db.commit()
        db.refresh(layer_meta)

        wms_base = (os.getenv("GEOSERVER_WMS_URL")).rstrip("/")
        wms_url = f"{wms_base}/{workspace.ws_name}/wms"
        full_layer_name = f"{workspace.ws_name}:{table_name}"

        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_PUBLISH_VECTOR_LAYER",
            resource_type="LAYER",
            resource_id=layer_meta.id,
            ip_address=client_ip,
            user_agent=user_agent,
            meta_data={"layer_name": layer_name, "table_name": table_name, "format": format_name}
        )

        return {
            "success": True,
            "message": f"Layer vektor '{layer_name}' berhasil disederhanakan dan dipublikasikan via S2S!",
            "data": {
                "id": layer_meta.id,
                "layer_name": full_layer_name,
                "display_name": layer_name,
                "geoserver_name": table_name,
                "workspace": workspace.ws_name,
                "type": "vector",
                "geom_type": dominant_geom,
                "data_type": format_name,
                "wms_url": wms_url,
                "wms_layers_param": full_layer_name,
                "bbox": [float(total_bounds[0]), float(total_bounds[1]), float(total_bounds[2]), float(total_bounds[3])],
                "simplification": stats,
                "created_at": layer_meta.created_at,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Terjadi kesalahan internal upload vektor: {e}")
