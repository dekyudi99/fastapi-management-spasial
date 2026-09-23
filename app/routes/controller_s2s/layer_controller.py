from fastapi import APIRouter, HTTPException, Depends, Query, Request, status
from fastapi.responses import Response, FileResponse
from typing import Optional, List
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text
import os
import secrets
import re
import httpx

from config.database import get_db, engine
from config.geoserver_auth import get_geoserver_connection
from models.layer import Layer
from models.workspace import Workspace
from models.project import Project
from models.api_key import ApiKey
from services.api_key_service import verify_api_key
from services.hash_id import decode_id
from services.log_service import create_log
from services.sld_to_layer import apply_sld_to_layer, generate_raster_sld

router = APIRouter()
geo = get_geoserver_connection()


# ── 3. GET /layers (Daftar Layer Pengguna untuk Multi-User Client) ─────────

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
        wms_base = (os.getenv("GEOSERVER_WMS_URL") or "").rstrip("/")

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
                "layer_type": layer.layer_type,
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


# ── 3B. DELETE /layers/{layer_id} (Hapus Layer via S2S) ───────────────────

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

        # 1. Hapus dari GeoServer & PostGIS jika ada
        if workspace and layer.geoserver_name:
            if layer.layer_type == "vector":
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f'DROP TABLE IF EXISTS public."{layer.geoserver_name}" CASCADE;'))
                except Exception as de:
                    print(f"[S2S] Peringatan: Gagal menghapus tabel PostGIS: {de}")
                try:
                    actual_store = "postgis_geosocial" if workspace.ws_name == "geosocial" else "postgis_store"
                    geoserver_url = (os.getenv("GEOSERVER_URL") or "").rstrip("/")
                    user = os.getenv("GEOSERVER_USER", "admin")
                    password = os.getenv("GEOSERVER_PASS", "geoserver")
                    httpx.delete(
                        f"{geoserver_url}/rest/workspaces/{workspace.ws_name}/datastores/{actual_store}/featuretypes/{layer.geoserver_name}?recurse=true",
                        auth=(user, password),
                        timeout=15.0
                    )
                except Exception as fe:
                    print(f"[S2S] Peringatan: Gagal menghapus featuretype GeoServer: {fe}")
            else:
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


# ── 3B.2. GET /layers/{layer_id}/download (Unduh Layer GeoTIFF atau PNG) ───

@router.get("/layers/{layer_id}/download")
async def s2s_download_layer(
    layer_id: str,
    format: str = Query("tiff", description="Format download: tiff atau png"),
    styled: bool = Query(True, description="Sertakan style SLD (True) atau data/grayscale asli (False)"),
    width: Optional[int] = Query(None, description="Resolusi lebar gambar"),
    height: Optional[int] = Query(None, description="Resolusi tinggi gambar"),
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Mengunduh file raster layer spasial dalam format GeoTIFF (.tif) atau PNG (.png),
    baik dengan style SLD yang diterapkan maupun data mentah/asli (raw/unstyled).
    """
    try:
        actual_id = int(layer_id) if str(layer_id).isdigit() else decode_id(layer_id)
        if actual_id is None:
            raise HTTPException(status_code=400, detail="ID Layer tidak valid.")

        layer = (
            db.query(Layer)
            .join(Workspace, Workspace.id == Layer.workspace_id)
            .filter(Layer.id == actual_id, Workspace.project_id == api_key.project_id)
            .first()
        )
        if not layer:
            raise HTTPException(status_code=404, detail="Layer tidak ditemukan atau tidak memiliki hak akses.")

        workspace = db.query(Workspace).filter(Workspace.id == layer.workspace_id).first()
        ws_name = workspace.ws_name if workspace else "default"

        clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', layer.name or f"layer_{actual_id}").strip('_')
        is_tiff = format.lower() in ("tiff", "tif", "geotiff")
        ext = "tif" if is_tiff else "png"
        style_suffix = "styled" if styled else "raw"
        filename = f"{clean_name}_{style_suffix}.{ext}"

        # Jika user memilih format GeoTIFF raw/unstyled dan file aslinya ada di disk
        if is_tiff and not styled and layer.file_path and os.path.exists(layer.file_path):
            return FileResponse(
                path=layer.file_path,
                filename=filename,
                media_type="image/tiff",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'}
            )

        # Hitung koordinat BBOX
        minx = db.query(func.ST_XMin(layer.bbox)).scalar()
        miny = db.query(func.ST_YMin(layer.bbox)).scalar()
        maxx = db.query(func.ST_XMax(layer.bbox)).scalar()
        maxy = db.query(func.ST_YMax(layer.bbox)).scalar()

        if minx is None or maxx is None or minx == maxx:
            minx, miny, maxx, maxy = 95.0, -11.0, 141.0, 6.0  # Fallback Indonesia BBOX

        epsg = layer.epsg or 4326
        w = width or layer.width or 1024
        h = height or layer.height or 1024
        w = min(max(int(w), 128), 4096)
        h = min(max(int(h), 128), 4096)

        geoserver_url = os.getenv("GEOSERVER_URL").rstrip("/")
        wms_endpoint = f"{geoserver_url}/{ws_name}/wms"

        style_param = "" if styled else "raster"
        format_param = "image/geotiff" if is_tiff else "image/png"

        params = {
            "service": "WMS",
            "version": "1.1.1",
            "request": "GetMap",
            "layers": f"{ws_name}:{layer.geoserver_name}",
            "styles": style_param,
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "width": str(w),
            "height": str(h),
            "srs": f"EPSG:{epsg}",
            "format": format_param,
        }
        if not is_tiff:
            params["transparent"] = "true"

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(wms_endpoint, params=params)
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"GeoServer gagal merender berkas unduhan: {resp.text[:200]}"
                )

            media_type = "image/tiff" if is_tiff else "image/png"
            output_content = resp.content

            # Jika format PNG dan memiliki colormap palette (PNG8), konversi ke 32-bit RGBA murni
            # agar kompatibel dengan seluruh image viewer di Windows (Photos, Paint) dan tidak blank/hitam
            if not is_tiff and len(output_content) > 0:
                try:
                    import io
                    import rasterio
                    import numpy as np
                    with rasterio.open(io.BytesIO(output_content)) as src:
                        if src.count == 1 and src.colormap(1):
                            data = src.read(1)
                            cm = src.colormap(1)
                            h, w = src.height, src.width
                            rgba = np.zeros((h, w, 4), dtype=np.uint8)
                            for k, v in cm.items():
                                rgba[data == k] = v
                            
                            prof = src.profile.copy()
                            prof.update(driver='PNG', count=4, dtype='uint8', nodata=None)
                            out_buf = io.BytesIO()
                            with rasterio.open(out_buf, 'w', **prof) as dst:
                                for band_idx in range(4):
                                    dst.write(rgba[:, :, band_idx], band_idx + 1)
                            output_content = out_buf.getvalue()
                except Exception as conv_err:
                    print(f"Warning: Gagal konversi PNG ke RGBA: {conv_err}")

            return Response(
                content=output_content,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Cache-Control": "no-cache",
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memproses unduhan layer: {str(e)}")


# ── 3C. GET /workspaces (Daftar Workspace untuk Project API Key) ───────────

@router.get("/workspaces")
def s2s_get_workspaces(
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    ## Ambil Daftar Workspace yang Tersedia untuk Project API Key Ini
    """
    try:
        workspaces = (
            db.query(Workspace)
            .filter(Workspace.project_id == api_key.project_id)
            .order_by(Workspace.id.desc())
            .all()
        )
        data = [
            {
                "id": ws.id,
                "name": ws.name,
                "ws_name": ws.ws_name,
                "created_at": ws.created_at,
            }
            for ws in workspaces
        ]
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil daftar workspace: {str(e)}")


class S2SCreateWorkspaceRequest(BaseModel):
    name: str

class S2SUpdateWorkspaceRequest(BaseModel):
    name: str

@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
def s2s_create_workspace(
    req: S2SCreateWorkspaceRequest,
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    ## Tambah Workspace Baru via S2S
    """
    try:
        clean_name = req.name.strip()
        if not clean_name:
            raise HTTPException(status_code=400, detail="Nama workspace tidak boleh kosong.")

        ws_technical = f"ws_{secrets.token_hex(4)}"
        success = geo.create_workspace(workspace=ws_technical)
        if not success:
            raise HTTPException(status_code=500, detail="Gagal membuat workspace di GeoServer.")

        new_ws = Workspace(
            project_id=api_key.project_id,
            name=clean_name,
            ws_name=ws_technical
        )
        db.add(new_ws)
        db.commit()
        db.refresh(new_ws)

        return {
            "success": True,
            "detail": f"Workspace '{clean_name}' berhasil dibuat!",
            "data": {
                "id": new_ws.id,
                "name": new_ws.name,
                "ws_name": new_ws.ws_name,
                "created_at": new_ws.created_at,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal membuat workspace: {str(e)}")


@router.put("/workspaces/{workspace_id}")
def s2s_update_workspace(
    workspace_id: str,
    req: S2SUpdateWorkspaceRequest,
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    ## Edit Nama Workspace via S2S
    """
    try:
        actual_id = int(workspace_id) if str(workspace_id).isdigit() else decode_id(workspace_id)
        if actual_id is None:
            raise HTTPException(status_code=400, detail="ID Workspace tidak valid.")

        clean_name = req.name.strip()
        if not clean_name:
            raise HTTPException(status_code=400, detail="Nama workspace tidak boleh kosong.")

        ws = (
            db.query(Workspace)
            .filter(Workspace.id == actual_id, Workspace.project_id == api_key.project_id)
            .first()
        )
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan atau akses ditolak.")

        ws.name = clean_name
        db.commit()

        return {
            "success": True,
            "detail": f"Nama workspace berhasil diperbarui menjadi '{clean_name}'!",
            "data": {
                "id": ws.id,
                "name": ws.name,
                "ws_name": ws.ws_name,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal memperbarui workspace: {str(e)}")


@router.delete("/workspaces/{workspace_id}")
def s2s_delete_workspace(
    workspace_id: str,
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    ## Hapus Workspace beserta Layernya via S2S
    """
    try:
        actual_id = int(workspace_id) if str(workspace_id).isdigit() else decode_id(workspace_id)
        if actual_id is None:
            raise HTTPException(status_code=400, detail="ID Workspace tidak valid.")

        ws = (
            db.query(Workspace)
            .filter(Workspace.id == actual_id, Workspace.project_id == api_key.project_id)
            .first()
        )
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan atau akses ditolak.")

        ws_title = ws.name
        ws_tech = ws.ws_name

        try:
            geo.delete_workspace(workspace=ws_tech)
        except Exception as ge:
            print(f"[S2S] Peringatan: GeoServer delete workspace gagal: {ge}")

        db.query(Layer).filter(Layer.workspace_id == ws.id).delete()
        db.delete(ws)
        db.commit()

        return {
            "success": True,
            "detail": f"Workspace '{ws_title}' dan semua layernya berhasil dihapus."
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal menghapus workspace: {str(e)}")


# ── 3D. POST /layers/{layer_id}/style (Update Style Layer via S2S) ─────────

class ColorEntryItem(BaseModel):
    quantity: float
    color: str
    opacity: float = 1.0
    label: Optional[str] = ""

class S2SUpdateRasterStyleRequest(BaseModel):
    style_type: Optional[str] = "values" # "values", "intervals", or "ramp"
    colors: Optional[List[ColorEntryItem]] = None
    style_sld: Optional[str] = None

@router.post("/layers/{layer_id}/style")
def s2s_update_layer_style(
    layer_id: str,
    req: S2SUpdateRasterStyleRequest,
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    ## Perbarui Style SLD Raster Layer via S2S
    Menerima rules warna (ColorEntry) atau XML SLD kustom untuk diterapkan ke GeoServer.
    """
    client_ip = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent") if request else None

    try:
        actual_id = int(layer_id) if str(layer_id).isdigit() else decode_id(layer_id)
        if actual_id is None:
            raise HTTPException(status_code=400, detail="ID Layer tidak valid.")

        layer = (
            db.query(Layer)
            .join(Workspace, Workspace.id == Layer.workspace_id)
            .filter(Layer.id == actual_id, Workspace.project_id == api_key.project_id)
            .first()
        )
        if not layer:
            raise HTTPException(status_code=404, detail="Layer tidak ditemukan atau akses ditolak.")

        workspace = db.query(Workspace).filter(Workspace.id == layer.workspace_id).first()
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan.")

        style_name = f"style_{layer.geoserver_name}"

        if req.style_sld and req.style_sld.strip():
            apply_sld_to_layer(
                workspace=workspace.ws_name,
                layer_name=layer.geoserver_name,
                style_name=style_name,
                sld_xml=req.style_sld.strip(),
            )
        elif req.colors and len(req.colors) > 0:
            sld_xml = generate_raster_sld(
                style_name=style_name,
                color_entries=[item.dict() for item in req.colors],
                style_type=req.style_type or "values",
            )
            apply_sld_to_layer(
                workspace=workspace.ws_name,
                layer_name=layer.geoserver_name,
                style_name=style_name,
                sld_xml=sld_xml,
            )
        else:
            raise HTTPException(status_code=400, detail="Harap berikan 'colors' atau 'style_sld'.")

        create_log(
            db=db,
            auth_type="API_KEY",
            action="S2S_UPDATE_LAYER_STYLE",
            resource_type="LAYER",
            resource_id=str(actual_id),
            resource_name=layer.name,
            status="SUCCESS",
            api_key_id=api_key.id,
            project_id=api_key.project_id,
            ip_address=client_ip,
            user_agent=user_agent,
            meta_data={"layer_id": actual_id, "style_name": style_name, "style_type": req.style_type},
        )

        return {
            "success": True,
            "detail": f"Style untuk layer '{layer.name}' berhasil diperbarui!",
            "style_name": style_name,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memperbarui style layer: {str(e)}")

