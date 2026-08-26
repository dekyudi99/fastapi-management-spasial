from config.geoserver_auth import get_geoserver_connection
from fastapi import APIRouter, Depends
from models.users import Users
from services.auth_service import get_current_user

router = APIRouter(prefix="/store", tags=["Store"])
geo = get_geoserver_connection()

# Untuk melihat daftar store yang sudah ada
@router.get("/list")
def list_stores(
    current_user: Users = Depends(get_current_user)
):
    response = geo.get_datastores()
    return response

# Untuk melihat detail sebuah store tertentu
@router.get("/store/{store_name}")
def get_store_metadata(
    name: str,
    current_user: Users = Depends(get_current_user)
):
    store = geo.get_datastore(store_name=name)
    return store