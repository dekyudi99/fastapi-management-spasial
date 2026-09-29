from fastapi import APIRouter
from services.geoserver_service_client import geoserver_service_client

router = APIRouter(prefix="/info", tags=["Info"])

# Menampilkan versi GeoServer
@router.get("/version")
def get_version():
    return geoserver_service_client.get_version()

# Menampilkan status GeoServer
@router.get("/status")
def get_status():
    return geoserver_service_client.get_status()