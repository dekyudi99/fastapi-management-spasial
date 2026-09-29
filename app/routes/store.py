from fastapi import APIRouter, Depends
from models.users import Users
from services.auth_service import get_current_user
from services.geoserver_service_client import geoserver_service_client

router = APIRouter(prefix="/store", tags=["Store"])

# Untuk melihat daftar store yang sudah ada
@router.get("/list")
def list_stores(
    current_user: Users = Depends(get_current_user)
):
    return geoserver_service_client.list_datastores()

# Untuk melihat detail sebuah store tertentu
@router.get("/store/{store_name}")
def get_store_metadata(
    store_name: str,
    current_user: Users = Depends(get_current_user)
):
    store = geoserver_service_client.get_datastore(store_name=store_name)
    return store or {}