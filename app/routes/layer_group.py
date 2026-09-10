from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel, Field
import os

from config.database import get_db
from models.users import Users
from models.project import Project
from models.workspace import Workspace
from models.layer import Layer
from models.layer_group import LayerGroup, LayerGroupLayer
from services.auth_service import get_current_user
from services.hash_id import decode_id, encode_id
from services.layer_group_service import (
    create_geoserver_layergroup,
    add_layer_to_geoserver_group,
    remove_layer_from_geoserver_group,
    delete_geoserver_layergroup,
    get_geoserver_layergroup
)

router = APIRouter(prefix="/layer-group", tags=["Layer Group"])

wms_base = os.getenv("GEOSERVER_WMS_URL", "http://localhost:8080/geoserver")

# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class CreateLayerGroupRequest(BaseModel):
    workspace_id: str = Field(..., description="ID atau hashed ID workspace")
    name: str = Field(..., min_length=1, max_length=150, description="Nama unik teknis di GeoServer")
    title: str = Field(..., min_length=1, max_length=200, description="Judul tampilan layer group")
    abstract_text: Optional[str] = Field(None, description="Deskripsi layer group")
    mode: Optional[str] = Field("single", description="Mode WMS: 'single', 'named', 'container', 'eo'")
    layer_ids: List[int] = Field(..., min_length=1, description="Daftar ID layer anggota (diurutkan dari layer paling bawah ke atas)")
    keywords: Optional[List[str]] = Field(default_factory=list, description="Tag pencarian")

class AddLayerToGroupRequest(BaseModel):
    layer_id: int
    style_name: Optional[str] = None

class UpdateLayerGroupRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    abstract_text: Optional[str] = None
    mode: Optional[str] = None
    layer_ids: Optional[List[int]] = Field(None, description="Daftar ID layer anggota terurut")
    keywords: Optional[List[str]] = None

# ── Endpoint: Create Layer Group ──────────────────────────────────────────────

@router.post("/create", status_code=status.HTTP_201_CREATED)
def create_layer_group(
    req: CreateLayerGroupRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):
    try:
        # 1. Validasi Workspace & Kepemilikan
        actual_ws_id = int(req.workspace_id) if str(req.workspace_id).isdigit() else decode_id(req.workspace_id)
        if actual_ws_id is None:
            raise HTTPException(status_code=400, detail="ID Workspace tidak valid!")

        workspace = (
            db.query(Workspace)
            .join(Project, Project.id == Workspace.project_id)
            .filter(Workspace.id == actual_ws_id, Project.user_id == current_user.id)
            .first()
        )
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan atau Anda tidak memiliki akses!")

        # 2. Cek apakah nama layer group sudah ada di workspace ini
        existing_group = (
            db.query(LayerGroup)
            .filter(LayerGroup.workspace_id == actual_ws_id, LayerGroup.name == req.name)
            .first()
        )
        if existing_group:
            raise HTTPException(status_code=400, detail=f"Layer Group dengan nama '{req.name}' sudah ada di workspace ini!")

        # 3. Validasi Layer-layer anggota
        layers = (
            db.query(Layer)
            .filter(Layer.id.in_(req.layer_ids), Layer.workspace_id == actual_ws_id)
            .all()
        )
        if len(layers) != len(req.layer_ids):
            found_ids = {l.id for l in layers}
            missing_ids = [lid for lid in req.layer_ids if lid not in found_ids]
            raise HTTPException(
                status_code=400,
                detail=f"Beberapa layer tidak ditemukan di workspace ini: {missing_ids}"
            )

        # Urutkan layer sesuai urutan dalam req.layer_ids (di mana #1 adalah layer teratas pada visual peta)
        layer_dict = {l.id: l for l in layers}
        ordered_layers = [layer_dict[lid] for lid in req.layer_ids]
        # Di GeoServer WMS, layer yang dirender paling akhir adalah yang tampil paling atas.
        # Maka kirim ke GeoServer dalam urutan terbalik agar layer #1 dirender paling atas.
        geoserver_layer_names = [l.geoserver_name for l in reversed(ordered_layers)]

        # 4. Buat Layer Group di GeoServer
        create_geoserver_layergroup(
            name=req.name,
            title=req.title,
            workspace=workspace.ws_name,
            layers=geoserver_layer_names,
            mode=req.mode or "single",
            abstract_text=req.abstract_text,
            keywords=req.keywords
        )

        # 5. Simpan ke Database
        layer_group = LayerGroup(
            workspace_id=actual_ws_id,
            name=req.name,
            title=req.title,
            abstract_text=req.abstract_text,
            mode=req.mode or "single",
            keywords=req.keywords
        )
        db.add(layer_group)
        db.flush()  # Dapatkan layer_group.id

        # Simpan relasi anggota layer dengan urutan
        for order, layer_obj in enumerate(ordered_layers):
            junction = LayerGroupLayer(
                layer_group_id=layer_group.id,
                layer_id=layer_obj.id,
                layer_order=order,
                style_name=None
            )
            db.add(junction)

        db.commit()

        wms_url = f"{wms_base}/{workspace.ws_name}/wms"

        return {
            "success": True,
            "detail": f"Layer Group '{req.title}' berhasil dibuat!",
            "data": {
                "id": layer_group.id,
                "name": layer_group.name,
                "title": layer_group.title,
                "workspace_id": actual_ws_id,
                "workspace_name": workspace.name,
                "mode": layer_group.mode,
                "total_layers": len(ordered_layers),
                "wms_url": wms_url,
                "wms_layers_param": f"{workspace.ws_name}:{layer_group.name}"
            }
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ── Endpoint: List Layer Groups ───────────────────────────────────────────────

@router.get("/list")
def list_layer_groups(
    workspace_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):
    try:
        base_query = (
            db.query(LayerGroup, Workspace)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(Project.user_id == current_user.id)
        )

        if workspace_id:
            actual_ws_id = int(workspace_id) if str(workspace_id).isdigit() else decode_id(workspace_id)
            if actual_ws_id is not None:
                base_query = base_query.filter(Workspace.id == actual_ws_id)

        total_count = base_query.count()
        offset = (page - 1) * size

        groups = (
            base_query
            .order_by(LayerGroup.created_at.desc())
            .offset(offset)
            .limit(size)
            .all()
        )

        result = []
        for group, ws in groups:
            layer_count = db.query(LayerGroupLayer).filter(LayerGroupLayer.layer_group_id == group.id).count()

            # Ambil metadata layer teratas (order = 0) untuk acuan bounding box & koordinat movefly map
            top_layer_info = (
                db.query(
                    Layer.id,
                    Layer.name,
                    Layer.epsg,
                    func.ST_XMin(Layer.bbox).label("minx"),
                    func.ST_YMin(Layer.bbox).label("miny"),
                    func.ST_XMax(Layer.bbox).label("maxx"),
                    func.ST_YMax(Layer.bbox).label("maxy"),
                )
                .join(LayerGroupLayer, LayerGroupLayer.layer_id == Layer.id)
                .filter(LayerGroupLayer.layer_group_id == group.id)
                .order_by(LayerGroupLayer.layer_order.asc())
                .first()
            )

            bbox = (
                [top_layer_info.minx, top_layer_info.miny, top_layer_info.maxx, top_layer_info.maxy]
                if top_layer_info and top_layer_info.minx is not None
                else None
            )
            epsg = top_layer_info.epsg if top_layer_info else 4326
            top_layer_name = top_layer_info.name if top_layer_info else None

            result.append({
                "id": group.id,
                "workspace_id": group.workspace_id,
                "workspace_name": ws.name,
                "ws_name": ws.ws_name,
                "name": group.name,
                "title": group.title,
                "abstract_text": group.abstract_text,
                "mode": group.mode,
                "keywords": group.keywords,
                "layer_count": layer_count,
                "bbox": bbox,
                "epsg": epsg,
                "top_layer_name": top_layer_name,
                "wms_url": f"{wms_base}/{ws.ws_name}/wms",
                "wms_layers_param": f"{ws.ws_name}:{group.name}",
                "created_at": group.created_at
            })

        return {
            "success": True,
            "detail": "Daftar Layer Group berhasil ditampilkan",
            "data": result,
            "pagination": {
                "total": total_count,
                "page": page,
                "size": size,
                "total_pages": (total_count + size - 1) // size if size else 0
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Endpoint: Detail Layer Group ──────────────────────────────────────────────

@router.get("/{group_id}")
def get_layer_group_detail(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):
    try:
        group_item = (
            db.query(LayerGroup, Workspace)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(LayerGroup.id == group_id, Project.user_id == current_user.id)
            .first()
        )

        if not group_item:
            raise HTTPException(status_code=404, detail="Layer Group tidak ditemukan!")

        group, ws = group_item

        # Ambil daftar layer anggota terurut beserta bbox
        junctions = (
            db.query(
                LayerGroupLayer,
                Layer,
                func.ST_XMin(Layer.bbox).label("minx"),
                func.ST_YMin(Layer.bbox).label("miny"),
                func.ST_XMax(Layer.bbox).label("maxx"),
                func.ST_YMax(Layer.bbox).label("maxy"),
            )
            .join(Layer, Layer.id == LayerGroupLayer.layer_id)
            .filter(LayerGroupLayer.layer_group_id == group.id)
            .order_by(LayerGroupLayer.layer_order.asc())
            .all()
        )

        layers_detail = [
            {
                "layer_id": layer.id,
                "layer_name": layer.name,
                "geoserver_name": layer.geoserver_name,
                "layer_order": junc.layer_order,
                "style_name": junc.style_name,
                "layer_type": layer.layer_type,
                "data_type": layer.data_type,
                "epsg": layer.epsg,
                "bbox": [minx, miny, maxx, maxy] if minx is not None else None,
            }
            for junc, layer, minx, miny, maxx, maxy in junctions
        ]

        top_bbox = layers_detail[0]["bbox"] if len(layers_detail) > 0 else None
        top_epsg = layers_detail[0]["epsg"] if len(layers_detail) > 0 else 4326

        return {
            "success": True,
            "data": {
                "id": group.id,
                "workspace_id": group.workspace_id,
                "workspace_name": ws.name,
                "ws_name": ws.ws_name,
                "name": group.name,
                "title": group.title,
                "abstract_text": group.abstract_text,
                "mode": group.mode,
                "keywords": group.keywords,
                "layers": layers_detail,
                "bbox": top_bbox,
                "epsg": top_epsg,
                "wms_url": f"{wms_base}/{ws.ws_name}/wms",
                "wms_layers_param": f"{ws.ws_name}:{group.name}",
                "created_at": group.created_at,
                "updated_at": group.updated_at
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Endpoint: Add Layer to Existing Group ─────────────────────────────────────

@router.post("/{group_id}/add-layer")
def add_layer_to_group(
    group_id: int,
    req: AddLayerToGroupRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):
    try:
        group_item = (
            db.query(LayerGroup, Workspace)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(LayerGroup.id == group_id, Project.user_id == current_user.id)
            .first()
        )
        if not group_item:
            raise HTTPException(status_code=404, detail="Layer Group tidak ditemukan!")

        group, ws = group_item

        # Cek apakah layer valid di workspace ini
        layer = db.query(Layer).filter(Layer.id == req.layer_id, Layer.workspace_id == ws.id).first()
        if not layer:
            raise HTTPException(status_code=404, detail="Layer tidak ditemukan di workspace ini!")

        # Cek apakah sudah tergabung
        existing = (
            db.query(LayerGroupLayer)
            .filter(LayerGroupLayer.layer_group_id == group.id, LayerGroupLayer.layer_id == req.layer_id)
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="Layer sudah tergabung di dalam Layer Group ini!")

        # Hitung layer_order terakhir
        current_max_order = (
            db.query(LayerGroupLayer.layer_order)
            .filter(LayerGroupLayer.layer_group_id == group.id)
            .order_by(LayerGroupLayer.layer_order.desc())
            .first()
        )
        new_order = (current_max_order[0] + 1) if current_max_order else 0

        # Panggil GeoServer
        add_layer_to_geoserver_group(
            layergroup_name=group.name,
            workspace=ws.ws_name,
            layer_name=layer.geoserver_name
        )

        # Simpan ke DB
        junction = LayerGroupLayer(
            layer_group_id=group.id,
            layer_id=layer.id,
            layer_order=new_order,
            style_name=req.style_name
        )
        db.add(junction)
        db.commit()

        return {
            "success": True,
            "detail": f"Layer '{layer.name}' berhasil ditambahkan ke Layer Group '{group.title}'!"
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ── Endpoint: Remove Layer from Existing Group ────────────────────────────────

@router.delete("/{group_id}/remove-layer/{layer_id}")
def remove_layer_from_group(
    group_id: int,
    layer_id: int,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):
    try:
        group_item = (
            db.query(LayerGroup, Workspace)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(LayerGroup.id == group_id, Project.user_id == current_user.id)
            .first()
        )
        if not group_item:
            raise HTTPException(status_code=404, detail="Layer Group tidak ditemukan!")

        group, ws = group_item

        junction = (
            db.query(LayerGroupLayer, Layer)
            .join(Layer, Layer.id == LayerGroupLayer.layer_id)
            .filter(LayerGroupLayer.layer_group_id == group.id, LayerGroupLayer.layer_id == layer_id)
            .first()
        )
        if not junction:
            raise HTTPException(status_code=404, detail="Layer tidak terdaftar di Layer Group ini!")

        junc_obj, layer_obj = junction

        # Panggil GeoServer
        remove_layer_from_geoserver_group(
            layergroup_name=group.name,
            workspace=ws.ws_name,
            layer_name=layer_obj.geoserver_name
        )

        # Hapus dari DB
        db.delete(junc_obj)
        db.commit()

        return {
            "success": True,
            "detail": f"Layer '{layer_obj.name}' berhasil dihapus dari Layer Group '{group.title}'!"
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ── Endpoint: Update Layer Group (Edit Meta & Member Layers) ──────────────────

@router.put("/{group_id}")
def update_layer_group(
    group_id: int,
    req: UpdateLayerGroupRequest,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):
    try:
        group_item = (
            db.query(LayerGroup, Workspace)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(LayerGroup.id == group_id, Project.user_id == current_user.id)
            .first()
        )
        if not group_item:
            raise HTTPException(status_code=404, detail="Layer Group tidak ditemukan!")

        group, ws = group_item

        if req.title is not None:
            group.title = req.title
        if req.abstract_text is not None:
            group.abstract_text = req.abstract_text
        if req.mode is not None:
            group.mode = req.mode
        if req.keywords is not None:
            group.keywords = req.keywords

        # Jika ada pembaruan susunan layer anggota
        if req.layer_ids is not None:
            if len(req.layer_ids) == 0:
                raise HTTPException(status_code=400, detail="Layer Group minimal harus memiliki 1 layer anggota!")

            layers = (
                db.query(Layer)
                .filter(Layer.id.in_(req.layer_ids), Layer.workspace_id == ws.id)
                .all()
            )
            if len(layers) != len(req.layer_ids):
                found_ids = {l.id for l in layers}
                missing = [lid for lid in req.layer_ids if lid not in found_ids]
                raise HTTPException(status_code=400, detail=f"Beberapa layer tidak ditemukan di workspace ini: {missing}")

            # Urutkan layer sesuai urutan req.layer_ids (#1 = teratas pada peta)
            layer_dict = {l.id: l for l in layers}
            ordered_layers = [layer_dict[lid] for lid in req.layer_ids]
            # Kirim ke GeoServer terbalik agar layer #1 dirender paling atas pada visual WMS
            geoserver_layer_names = [l.geoserver_name for l in reversed(ordered_layers)]

            # Update di GeoServer: hapus group lama lalu buat ulang dengan layer & urutan baru
            delete_geoserver_layergroup(group.name, ws.ws_name)
            create_geoserver_layergroup(
                name=group.name,
                title=group.title,
                workspace=ws.ws_name,
                layers=geoserver_layer_names,
                mode=group.mode,
                abstract_text=group.abstract_text,
                keywords=group.keywords
            )

            # Update di Database: hapus junction lama lalu insert yang baru sesuai urutan
            db.query(LayerGroupLayer).filter(LayerGroupLayer.layer_group_id == group.id).delete()
            for order, layer_obj in enumerate(ordered_layers):
                junction = LayerGroupLayer(
                    layer_group_id=group.id,
                    layer_id=layer_obj.id,
                    layer_order=order,
                    style_name=None
                )
                db.add(junction)

        db.commit()

        return {
            "success": True,
            "detail": f"Layer Group '{group.title}' berhasil diperbarui!",
            "data": {
                "id": group.id,
                "name": group.name,
                "title": group.title,
                "mode": group.mode,
                "abstract_text": group.abstract_text,
            }
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ── Endpoint: Delete Layer Group ──────────────────────────────────────────────

@router.delete("/{group_id}")
def delete_layer_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):
    try:
        group_item = (
            db.query(LayerGroup, Workspace)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(LayerGroup.id == group_id, Project.user_id == current_user.id)
            .first()
        )
        if not group_item:
            raise HTTPException(status_code=404, detail="Layer Group tidak ditemukan!")

        group, ws = group_item

        # Hapus dari GeoServer
        delete_geoserver_layergroup(
            layergroup_name=group.name,
            workspace=ws.ws_name
        )

        # Hapus dari DB (cascade ke layer_group_layers)
        db.delete(group)
        db.commit()

        return {
            "success": True,
            "detail": f"Layer Group '{group.title}' berhasil dihapus!"
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
