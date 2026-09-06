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

import rasterio
from rasterio.enums import ColorInterp

def sanitize_tiff_for_geoserver(file_path: str):
    """
    Memastikan GeoTIFF memiliki tag photometric dan band interleave 
    yang kompatibel dengan Java ImageIO GeoServer.
    """
    with rasterio.open(file_path) as src:
        # Jika file memiliki 4 band tetapi tidak memiliki photometric RGB yang benar
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
