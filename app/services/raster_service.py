import rasterio
from shapely.geometry import box

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