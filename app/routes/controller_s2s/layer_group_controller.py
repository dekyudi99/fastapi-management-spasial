from fastapi import APIRouter, HTTPException, Depends, Query, Request, status
from typing import Optional, List, Any
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
import os
import uuid

from config.database import get_db
from models.layer import Layer
from models.workspace import Workspace
from models.project import Project
from models.api_key import ApiKey
from models.layer_group import LayerGroup, LayerGroupLayer
from services.layer_group_service import create_geoserver_layergroup, delete_geoserver_layergroup
from services.api_key_service import verify_api_key
from services.hash_id import decode_id, encode_id
from services.log_service import create_log

router = APIRouter()


# ── Pydantic Request Models ───────────────────────────────────────────────────

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


# ── 4. POST /layer-groups (Create Layer Group via S2S) ───────────────────

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
                "id": encode_id(layer_group.id),
                "raw_id": layer_group.id,
                "name": layer_group.name,
                "title": layer_group.title,
                "workspace_id": encode_id(workspace.id),
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


# ── 5. GET /layer-groups (List Layer Groups via S2S) ───────────────────────

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
                "id": encode_id(grp.id),
                "raw_id": grp.id,
                "name": grp.name,
                "title": grp.title,
                "abstract_text": grp.abstract_text,
                "workspace_id": encode_id(grp.workspace_id),
                "workspace_name": ws_name,
                "workspace_display_name": ws_display,
                "client_user_id": grp.client_user_id,
                "wms_url": f"{wms_base}/{ws_name}/wms",
                "wms_layers_param": f"{ws_name}:{grp.name}",
                "bbox": bbox,
                "layers": [
                    {
                        "id": encode_id(ml[0]),
                        "raw_id": ml[0],
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


# ── 6. DELETE /layer-groups/{hashed_id} ──────────────────────────────────────────

@router.delete("/layer-groups/{hashed_id}")
async def s2s_delete_layer_group(
    hashed_id: str,
    request: Request = None,
    api_key: ApiKey = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    try:
        grp_id = int(hashed_id) if str(hashed_id).isdigit() else decode_id(str(hashed_id))
        if grp_id is None:
            raise HTTPException(status_code=400, detail="Invalid Layer Group ID.")

        grp = (
            db.query(LayerGroup)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(LayerGroup.id == grp_id, Project.id == api_key.project_id)
            .first()
        )
        if not grp:
            raise HTTPException(status_code=404, detail="Layer Group not found.")

        workspace = db.query(Workspace).filter(Workspace.id == grp.workspace_id).first()
        if workspace:
            delete_geoserver_layergroup(grp.name, workspace=workspace.ws_name)

        db.delete(grp)
        db.commit()

        return {"success": True, "detail": f"Layer Group #{hashed_id} deleted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete layer group: {str(e)}")


# ── 7. PUT /layer-groups/{hashed_id} (Update Layer Group via S2S) ─────────────────

@router.put("/layer-groups/{hashed_id}")
async def s2s_update_layer_group(
    hashed_id: str,
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
        grp_id = int(hashed_id) if str(hashed_id).isdigit() else decode_id(str(hashed_id))
        if grp_id is None:
            raise HTTPException(status_code=400, detail="Invalid Layer Group ID.")

        query = (
            db.query(LayerGroup)
            .join(Workspace, Workspace.id == LayerGroup.workspace_id)
            .join(Project, Project.id == Workspace.project_id)
            .filter(LayerGroup.id == grp_id, Project.id == api_key.project_id)
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

            raw_ids = [int(lid) if str(lid).isdigit() else decode_id(str(lid)) for lid in req.layer_ids]
            valid_ids = [i for i in raw_ids if i is not None]

            # Cari layer anggota di workspace ini
            layers = (
                db.query(Layer)
                .filter(Layer.id.in_(valid_ids), Layer.workspace_id == workspace.id)
                .all()
            )
            layer_dict = {l.id: l for l in layers}
            valid_layers = [layer_dict[lid] for lid in valid_ids if lid in layer_dict]

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
                "id": encode_id(grp.id),
                "raw_id": grp.id,
                "name": grp.name,
                "title": grp.title,
                "abstract_text": grp.abstract_text,
                "workspace_id": encode_id(grp.workspace_id),
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
