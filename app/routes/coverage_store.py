from fastapi import APIRouter, Form, File, UploadFile, Depends, HTTPException, status
from sqlalchemy.orm import Session
import os
import shutil
import uuid
from services.raster_service import get_tiff_metadata
from models.workspace import Workspace
from models.project import Project
from models.layer import Layer
from config.database import get_db
from models.users import Users
from services.auth_service import get_current_user

FILE_PATH = "D:/proyek-gis/data_raster"

from services.geoserver_service_client import geoserver_service_client

router = APIRouter(prefix="/coveragestore", tags=["Coverage Store"])

#Untuk melihat daftar coverage store yang sudah ada
@router.get("/list")
async def list_coveragestores(
    current_user: Users = Depends(get_current_user)
):
    return geoserver_service_client.list_coveragestores()

#Untuk melihat detail sebuah coverage store tertentu
@router.get("/coveragestore/{store_name}")
def get_coveragestore_metadata(
    store_name: str,
    current_user: Users = Depends(get_current_user)
):
    store = geoserver_service_client.get_coveragestore(store_name=store_name)
    return store or {}

@router.post("/print/rasterio")
def get_rasterio(
    file: UploadFile = File(...),
    current_user: Users = Depends(get_current_user)
):
    file_extension = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_extension}"

    file_path = os.path.normpath(os.path.join(FILE_PATH, unique_filename))
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    meta = get_tiff_metadata(file_path)

    print(meta)
    return(meta)