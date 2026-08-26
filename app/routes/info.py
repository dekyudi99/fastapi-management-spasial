from config.geoserver_auth import get_geoserver_connection
from fastapi import APIRouter

router = APIRouter(prefix="/info", tags=["Info"])

geo = get_geoserver_connection()

# Menampilkan versi GeoServer
@router.get("/version")
def get_version():
    geo_version = geo.get_version()
    return geo_version

# Menampilkan status GeoServer
@router.get("/status")
def get_status():
    status = geo.get_status()
    return status