from fastapi import APIRouter, HTTPException, Depends
import os
import requests
import xmltodict
from dotenv import load_dotenv
from urllib.parse import quote
from models.users import Users
from services.auth_service import get_current_user

load_dotenv()

router = APIRouter(prefix="/service", tags=["Service"])

GEOSERVER_URL = "http://localhost:8080/geoserver"
USERNAME = os.getenv("GEOSERVER_USER")
PASSWORD = os.getenv("GEOSERVER_PASS")

def fetch_xml(url: str):
    response = requests.get(url, auth=(USERNAME, PASSWORD))

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text
        )
    return xmltodict.parse(response.text)

def ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]

def get_wms_capabilities():
    url = f"{GEOSERVER_URL}/ows?service=WMS&version=1.3.0&request=GetCapabilities"
    return fetch_xml(url)

@router.get("/wms")
def get_all_wms_layers(
    current_user: Users = Depends(get_current_user)
):
    data = get_wms_capabilities()

    root_layer = (
        data.get("WMS_Capabilities", {})
        .get("Capability", {})
        .get("Layer", {})
    )

    layers = ensure_list(root_layer.get("Layer"))

    result = []

    for layer in layers:
        name = layer.get("Name")

        if not name:
            continue

        result.append({
            "id": name,
            "name": name,
            "title": layer.get("Title"),
            "abstract": layer.get("Abstract"),
            "crs": layer.get("CRS"),
            "bbox": layer.get("EX_GeographicBoundingBox"),
            "styles": ensure_list(layer.get("Style")),
            "links": {
                "detail": f"/api/ogc/wms/{quote(name, safe='')}",
                "map_url": f"/api/ogc/wms/{quote(name, safe='')}/map-url"
            }
        })

    return {
        "service": "WMS",
        "total": len(result),
        "data": result
    }


@router.get("/wms/{layer_name}")
def get_wms_by_name(
    layer_name: str,
    current_user: Users = Depends(get_current_user)
):
    data = get_wms_capabilities()

    root_layer = (
        data.get("WMS_Capabilities", {})
        .get("Capability", {})
        .get("Layer", {})
    )

    layers = ensure_list(root_layer.get("Layer"))

    for layer in layers:
        if layer.get("Name") == layer_name:
            return {
                "service": "WMS",
                "data": {
                    "id": layer.get("Name"),
                    "name": layer.get("Name"),
                    "title": layer.get("Title"),
                    "abstract": layer.get("Abstract"),
                    "crs": layer.get("CRS"),
                    "bbox": layer.get("EX_GeographicBoundingBox"),
                    "bounding_box": layer.get("BoundingBox"),
                    "styles": ensure_list(layer.get("Style")),
                    "queryable": layer.get("@queryable"),
                    "opaque": layer.get("@opaque"),
                    "map_url": (
                        f"{GEOSERVER_URL}/wms?"
                        f"service=WMS&version=1.1.1&request=GetMap&"
                        f"layers={layer_name}&styles=&"
                        f"bbox=-180,-90,180,90&width=800&height=600&"
                        f"srs=EPSG:4326&format=image/png&transparent=true"
                    )
                }
            }

    raise HTTPException(status_code=404, detail="Layer WMS tidak ditemukan")


@router.get("/wms/{layer_name}/map-url")
def get_wms_map_url(
    layer_name: str,
    current_user: Users = Depends(get_current_user)
):
    return {
        "service": "WMS",
        "layer": layer_name,
        "url": (
            f"{GEOSERVER_URL}/wms?"
            f"service=WMS&version=1.1.1&request=GetMap&"
            f"layers={layer_name}&styles=&"
            f"bbox=-180,-90,180,90&width=800&height=600&"
            f"srs=EPSG:4326&format=image/png&transparent=true"
        )
    }
