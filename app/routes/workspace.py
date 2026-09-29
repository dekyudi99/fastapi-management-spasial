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
from pydantic import BaseModel
from typing import List, Optional
from services.sld_to_layer import generate_raster_sld, create_or_update_style, assign_style_to_layer, style_exists_in_geoserver
from services.log_service import create_log
from sqlalchemy.orm.attributes import flag_modified

from services.geoserver_service_client import geoserver_service_client

router = APIRouter(prefix="/workspace", tags=["Workspace"])

# Untuk membuat workspace baru di GeoServer
@router.post("/create/{hashed_id}", status_code=status.HTTP_201_CREATED)
def create_workspace(
    hashed_id: str,
    name_workspace: str = Form(...),
    visibility: str = Form("private"),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        if visibility not in ("private", "public"):
            raise HTTPException(status_code=400, detail="visibility harus 'private' atau 'public'")

        id = decode_id(hashed_id)

        project = db.query(Project).filter(Project.id == id, Project.user_id == current_user.id).first()

        if project is None:
            raise HTTPException(status_code=404, detail="Project tidak ditemukan!")

        workspace_name = f"ws_{secrets.token_hex(4)}"
        success = geoserver_service_client.create_workspace(workspace_name)

        if success:
            workspace = Workspace(
                project_id=id,
                name=name_workspace,
                ws_name=workspace_name,
                visibility=visibility
            )

            db.add(workspace)
            db.commit()
            db.refresh(workspace)

            create_log(
                db=db,
                auth_type="JWT",
                user_id=current_user.id,
                project_id=id,
                action="WORKSPACE_CREATE",
                resource_type="WORKSPACE",
                resource_id=str(workspace.id),
                resource_name=name_workspace,
                status="SUCCESS",
                client_user_name=current_user.username,
                client_user_email=current_user.email,
            )

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

        total_count = db.query(func.count(Workspace.id)).join(Project).filter(Project.id == id, Project.user_id == current_user.id).scalar()
                
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
                "layer_count": layer_count,
                "visibility": workspaces.visibility
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

# Fungsi untuk melihat seluruh workspace milik user (untuk dropdown/filter)
@router.get("/all")
def get_all_user_workspaces(
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        workspaces = (
            db.query(Workspace, Project.project_name)
            .join(Project, Project.id == Workspace.project_id)
            .filter(Project.user_id == current_user.id)
            .order_by(Workspace.name.asc())
            .all()
        )
        return {
            "success": True,
            "data": [
                {
                    "id": encode_id(ws.id),
                    "raw_id": ws.id,
                    "name": ws.name,
                    "ws_name": ws.ws_name,
                    "project_name": proj_name,
                }
                for ws, proj_name in workspaces
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class WorkspaceUpdateRequest(BaseModel):
    name: str
    visibility: Optional[str] = "private"

# Fungsi untuk melihat detail workspace
@router.get("/{hashed_id}")
def get_detail_workspace(
    hashed_id: str,
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
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan!")

        project = db.query(Project).filter(Project.id == workspace.project_id).first()

        # Cek apakah default style telah diatur secara eksplisit oleh pengguna
        ws_meta = workspace.metadata_json if isinstance(workspace.metadata_json, dict) else {}
        default_style_config = ws_meta.get("default_style")
        default_style_name = None
        if default_style_config and isinstance(default_style_config, dict):
            default_style_name = default_style_config.get("name")
            # Pastikan style masih ada di GeoServer
            if default_style_name and not style_exists_in_geoserver(default_style_name):
                default_style_name = None

        result = {
            "id": encode_id(workspace.id),
            "name": workspace.name,
            "ws_name": workspace.ws_name,
            "project_id": encode_id(workspace.project_id),
            "project_name": project.project_name if project else "",
            "project_description": project.description if project else "",
            "default_style": default_style_name,
            "default_style_config": default_style_config if default_style_name else None,
            "created_at": workspace.created_at,
            "visibility": workspace.visibility,
        }

        return {
            "success": True,
            "detail": "Get detail workspace is successful",
            "data": result
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Fungsi untuk memperbarui workspace (misal nama workspace)
@router.put("/{hashed_id}")
def update_workspace(
    hashed_id: str,
    req: WorkspaceUpdateRequest,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        if req.visibility not in ("private", "public"):
            raise HTTPException(status_code=400, detail="visibility harus 'private' atau 'public'")

        id = decode_id(hashed_id)
        workspace = (
            db.query(Workspace)
            .join(Project)
            .filter(Workspace.id == id, Project.user_id == current_user.id)
            .first()
        )
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan!")

        workspace.name = req.name
        workspace.visibility = req.visibility
        db.commit()
        db.refresh(workspace)

        return {
            "success": True,
            "detail": "Workspace berhasil diperbarui!",
            "data": {
                "id": encode_id(workspace.id),
                "name": workspace.name,
                "visibility": workspace.visibility,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

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

        success = geoserver_service_client.delete_workspace(workspace_name=workspace.ws_name)
        
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


class ColorEntryItem(BaseModel):
    quantity: float
    color: str
    opacity: float = 1.0
    label: Optional[str] = ""

class WorkspaceDefaultStyleRequest(BaseModel):
    style_type: Optional[str] = "values" # "values", "intervals", "ramp"
    colors: List[ColorEntryItem]
    apply_to_existing: Optional[bool] = False

# Simpan / update default palette style untuk workspace
@router.post("/style/{hashed_id}")
def save_workspace_default_style(
    hashed_id: str,
    req: WorkspaceDefaultStyleRequest,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        ws_id = decode_id(hashed_id)
        workspace = (
            db.query(Workspace)
            .join(Project)
            .filter(Workspace.id == ws_id, Project.user_id == current_user.id)
            .first()
        )
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan!")

        style_name = f"default_{workspace.ws_name}"

        # 1. Generate XML SLD
        sld_xml = generate_raster_sld(
            style_name=style_name,
            color_entries=[c.dict() for c in req.colors],
            style_type=req.style_type or "intervals"
        )

        # 2. Buat atau perbarui style di GeoServer
        create_or_update_style(style_name, sld_xml)

        # 3. Simpan konfigurasi default style ke metadata_json workspace
        if not workspace.metadata_json or not isinstance(workspace.metadata_json, dict):
            workspace.metadata_json = {}

        workspace.metadata_json["default_style"] = {
            "name": style_name,
            "style_type": req.style_type or "intervals",
            "colors": [c.dict() for c in req.colors],
            "updated_at": datetime.utcnow().isoformat()
        }
        flag_modified(workspace, "metadata_json")
        db.commit()

        # 4. HANYA terapkan ke layer yang BELUM memiliki style khusus (jika diminta)
        # Sesuai instruksi: layer yang sudah memiliki style TIDAK BOLEH terpengaruh!
        updated_count = 0
        if req.apply_to_existing:
            layers = db.query(Layer).filter(
                Layer.workspace_id == workspace.id,
                Layer.layer_type == "raster"
            ).all()

            for lyr in layers:
                meta = lyr.metadata_json if isinstance(lyr.metadata_json, dict) else {}
                # Jika layer sudah memiliki style custom (symbology), lewati!
                if meta.get("symbology") and not meta.get("symbology", {}).get("inherited_from_workspace"):
                    continue
                try:
                    assign_style_to_layer(workspace.ws_name, lyr.geoserver_name, style_name)
                    updated_count += 1
                except Exception as le:
                    print(f"Gagal mengaitkan style ke {lyr.name}: {le}")

        return {
            "success": True,
            "detail": f"Default style untuk workspace '{workspace.name}' berhasil disimpan!",
            "style_name": style_name,
            "updated_layers_count": updated_count
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error saving workspace style: {e}")
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan default style: {str(e)}")


# Endpoint untuk menjadikan style dari suatu layer sebagai Default Style Workspace
@router.post("/style-from-layer/{hashed_id}/{layer_id}")
def set_workspace_default_style_from_layer(
    hashed_id: str,
    layer_id: str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        ws_id = decode_id(hashed_id)
        workspace = (
            db.query(Workspace)
            .join(Project)
            .filter(Workspace.id == ws_id, Project.user_id == current_user.id)
            .first()
        )
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan!")

        actual_layer_id = int(layer_id) if str(layer_id).isdigit() else decode_id(layer_id)
        layer = db.query(Layer).filter(Layer.id == actual_layer_id, Layer.workspace_id == workspace.id).first()
        if not layer:
            raise HTTPException(status_code=404, detail="Layer tidak ditemukan pada workspace ini!")

        layer_meta = layer.metadata_json if isinstance(layer.metadata_json, dict) else {}
        symbology = layer_meta.get("symbology")
        if not symbology or not symbology.get("classes"):
            raise HTTPException(status_code=400, detail="Layer ini belum memiliki konfigurasi style tersimpan!")

        style_name = f"default_{workspace.ws_name}"
        color_entries = [
            {
                "quantity": float(c.get("quantity") if c.get("quantity") is not None else c.get("max", 0)),
                "color": c.get("color", "#000000"),
                "opacity": float(c.get("opacity", 1.0)),
                "label": c.get("label", "")
            }
            for c in symbology.get("classes", [])
        ]

        sld_xml = generate_raster_sld(
            style_name=style_name,
            color_entries=color_entries,
            style_type=symbology.get("style_type", "intervals")
        )
        create_or_update_style(style_name, sld_xml)

        if not workspace.metadata_json or not isinstance(workspace.metadata_json, dict):
            workspace.metadata_json = {}

        workspace.metadata_json["default_style"] = {
            "name": style_name,
            "style_type": symbology.get("style_type", "intervals"),
            "colors": color_entries,
            "source_layer_name": layer.name,
            "updated_at": datetime.utcnow().isoformat()
        }
        flag_modified(workspace, "metadata_json")
        db.commit()

        return {
            "success": True,
            "detail": f"Style dari layer '{layer.name}' berhasil dijadikan sebagai default style workspace!",
            "style_name": style_name
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# Endpoint untuk mereset / menghapus default style workspace (kembali ke None)
@router.delete("/style/{hashed_id}")
def reset_workspace_default_style(
    hashed_id: str,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        ws_id = decode_id(hashed_id)
        workspace = (
            db.query(Workspace)
            .join(Project)
            .filter(Workspace.id == ws_id, Project.user_id == current_user.id)
            .first()
        )
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan!")

        if workspace.metadata_json and isinstance(workspace.metadata_json, dict):
            workspace.metadata_json.pop("default_style", None)
            flag_modified(workspace, "metadata_json")
            db.commit()

        # Hapus style default_{ws} dari GeoServer jika ada
        try:
            geoserver_service_client.delete_style(style_name=f"default_{workspace.ws_name}")
        except Exception:
            pass

        return {
            "success": True,
            "detail": "Default style workspace berhasil direset ke None!"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
