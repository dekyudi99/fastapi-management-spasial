from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from models.api_key import ApiKey
from services.api_key_service import verify_api_key
import io
import rasterio
import json
from geopandas import gpd

router = APIRouter(prefix="/test", tags=["Test"])

@router.get("")
def test(
    api_key: ApiKey = Depends(verify_api_key)
):
    return {
        "success": True,
        "detail": "API Key valid",
        "project_id": api_key.project_id
    }

@router.get("/{id}")
def test_id(
    id: int
):
    return {
        "id": f"your id is not this {id}"
    }

@router.post("/inspect-file")
async def inspect_file(file: UploadFile = File(...)):
    try:
        filename = file.filename.lower()
        if filename.endswith(('.tif', '.tiff')):
            return ("Ini file geotiff!")
        elif filename.endswith(('.geojson')):
            content = await file.read()
            geojson_str = content.decode('utf-8')
            geojson_data = json.loads(geojson_str)

            return geojson_data
        
        # # Baca file langsung dari memory (bytes)
        # contents = await file.read()
        
        # with rasterio.open(io.BytesIO(contents)) as src:
        #     # Ambil profile/metadata dasar bawaan rasterio
        #     meta_data = dict(src.profile)
            
        #     # Konversi objek non-JSON serializable (seperti CRS & Affine Matrix) ke format standar
        #     meta_data["crs"] = str(src.crs)
        #     meta_data["epsg"] = src.crs.to_epsg() if src.crs else None
        #     meta_data["transform"] = [val for val in src.transform]
        #     meta_data["bounds"] = list(src.bounds)
        #     meta_data["resolution"] = list(src.res)

        #     # 2. Ambil metadata tags tambahan (misal info sensor, tanggal capture, GeoTIFF tags)
        #     tags_data = src.tags()
            
        #     # 3. Ambil tags per-band jika ada
        #     band_tags = {f"band_{i}": src.tags(i) for i in range(1, src.count + 1)}

        #     # 4. Ambil statistik cepat per band (min/max)
        #     stats = {}
        #     for i in range(1, src.count + 1):
        #         band_arr = src.read(i, masked=True)
        #         stats[f"band_{i}"] = {
        #             "min": float(band_arr.min()) if band_arr.count() > 0 else None,
        #             "max": float(band_arr.max()) if band_arr.count() > 0 else None,
        #             "mean": float(band_arr.mean()) if band_arr.count() > 0 else None,
        #             "std": float(band_arr.std()) if band_arr.count() > 0 else None,
        #         }

        #     return {
        #         "success": True,
        #         "filename": file.filename,
        #         "profile": meta_data,
        #         "tags": tags_data,
        #         "band_tags": band_tags,
        #         "statistics": stats
        #     }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal membaca file GeoTIFF: {str(e)}")