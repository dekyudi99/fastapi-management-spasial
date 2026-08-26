from config.geoserver_auth import get_geoserver_connection
from fastapi import APIRouter, Form, File, UploadFile, Depends, HTTPException, status
from sqlalchemy.orm import Session
import os
import shutil
import uuid
from services.raster_service import get_tiff_metadata
from models.raster_metadata import RasterMetadata
from models.workspace import Workspace
from models.project import Project
from models.layer import Layer
from geoalchemy2.shape import from_shape
from shapely.geometry import box
from config.database import get_db
from models.users import Users
from services.auth_service import get_current_user

router = APIRouter(prefix="/coveragestore", tags=["Coverage Store"])
geo = get_geoserver_connection()

FILE_PATH = "D:/proyek-gis/data_raster"

# Untuk membuat layer sekaligus store baru di GeoServer
@router.post("/publish-automated", status_code=status.HTTP_201_CREATED)
async def publish_raster(
    workspace_id: int = Form(...),
    description: str = Form(...),
    layer_name: str = Form(...),  
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Users = Depends(get_current_user)
):  
    try:
        workspace = (
            db.query(Workspace)
            .filter(Workspace.id == workspace_id)
            .join(Project)
            .filter(Project.user_id == current_user.id)
            .first()
        )

        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace tidak ditemukan!")

        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"

        file_path = os.path.normpath(os.path.join(FILE_PATH, unique_filename))
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        tiff_metadata = get_tiff_metadata(file_path)

        epsg = tiff_metadata["epsg"]
        bounds = tiff_metadata["bbox"]
        width=tiff_metadata["dimensions"]["width"]
        height=tiff_metadata["dimensions"]["height"]

        geom = box(
            bounds.left,
            bounds.bottom,
            bounds.right,
            bounds.top
        )

        meta =Layer(
            workspace_id=workspace.id,
            name=layer_name,
            description=description,
            geoserver_name=unique_filename,
            epsg=epsg,
            bbox=from_shape(geom, srid=tiff_metadata["epsg"]),
            width=width,
            height=height,
            layer_type="raster",
            data_type="GeoTiff",
            file_path=file_path,
            status="PUBLISHED"
        )

        db.add(meta)
        db.commit()

        success = geo.create_coveragestore(
            layer_name=unique_filename, 
            path=file_path, 
            workspace=workspace.ws_name
        )
        
        if success:
            print(f"Berhasil! Layer '{layer_name}' siap diakses via WMS.")
            # preview_url = f"http://localhost:8080/geoserver/{workspace}/wms?service=WMS&version=1.1.0&request=GetMap&layers={workspace}%3A{layer_name}&bbox={bounds.left}%2C{bounds.bottom}%2C{bounds.right}%2C{bounds.top}styles=&width={width}&height={height}&srs=EPSG:{epsg}&format=application/openlayers"
            result = f"Layer '{layer_name}' berhasil dipublikasikan di workspace '{workspace.ws_name}'"
        else:
            result = "Gagal mempublikasikan layer. Pastikan path file benar."
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"Gagal Menyimpan Data, Karena {e}")
        raise HTTPException(status_code=500, detail=f"Gagal Menyimpan Data, Karena {e}")



#Untuk melihat daftar coverage store yang sudah ada
@router.get("/list")
async def list_coveragestores(
    current_user: Users = Depends(get_current_user)
):
    response = geo.get_coveragestores()
    return response

#Untuk melihat detail sebuah coverage store tertentu
@router.get("/coveragestore/{store_name}")
def get_coveragestore_metadata(
    name: str,
    current_user: Users = Depends(get_current_user)
):
    store = geo.get_coveragestore(coveragestore_name=name)
    return store

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