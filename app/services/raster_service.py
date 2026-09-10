import rasterio
from shapely.geometry import box
from rasterio.enums import ColorInterp

def get_tiff_metadata(file_path: str):
    with rasterio.open(file_path) as dataset:
        epsg = dataset.crs.to_epsg()
        
        
        bbox = dataset.bounds

        width = dataset.width
        height = dataset.height
        
        return {
            "epsg": epsg,
            "bbox": bbox,
            "dimensions": {
                "width": width,
                "height": height
            }
        }

def validate_single_band(file_path: str) -> None:
    """
    Validasi bahwa file GeoTIFF hanya memiliki tepat 1 band.
    Raise ValueError jika lebih dari 1 band (multi-band RGB/RGBA tidak didukung S2S endpoint).
    """
    with rasterio.open(file_path) as dataset:
        band_count = dataset.count
        if band_count != 1:
            raise ValueError(
                f"File GeoTIFF harus 1 band (single-band). "
                f"File Anda memiliki {band_count} band. "
                f"Silakan konversi ke single-band terlebih dahulu."
            )


import rasterio
from rasterio.enums import ColorInterp

def sanitize_tiff_for_geoserver(file_path: str):
    with rasterio.open(file_path) as src:
        if src.count == 4 and src.colorinterp[0] == ColorInterp.gray:
            profile = src.profile.copy()
            profile.update(photometric='RGB', nodata=0)
            data = src.read()
            with rasterio.open(file_path, 'w', **profile) as dst:
                dst.write(data)
                dst.colorinterp = [
                    ColorInterp.red, 
                    ColorInterp.green, 
                    ColorInterp.blue, 
                    ColorInterp.alpha
                ]
