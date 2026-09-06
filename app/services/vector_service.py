import geopandas as gpd
import pandas as pd
from rasterio.transform import array_bounds
from shapely.geometry import box
from collections import namedtuple

# Tipe yang sama dengan rasterio.bounds agar konsisten dengan handler TIFF
BoundingBox = namedtuple("BoundingBox", ["left", "bottom", "right", "top"])


def get_vector_metadata(file_path: str) -> dict:
    """
    Baca file vector (GeoJSON, GeoPackage, atau direktori Shapefile)
    menggunakan geopandas dan kembalikan epsg serta bbox.

    Returns:
        {
            "epsg": int,
            "bbox": BoundingBox(left, bottom, right, top)
        }
    """
    gdf = gpd.read_file(file_path)

    # Pastikan CRS tersedia; jika tidak ada, asumsikan WGS84
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)

    epsg = gdf.crs.to_epsg()

    total_bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
    bbox = BoundingBox(
        left=total_bounds[0],
        bottom=total_bounds[1],
        right=total_bounds[2],
        top=total_bounds[3],
    )

    return {
        "epsg": epsg,
        "bbox": bbox,
    }


def prepare_csv_as_geopackage(
    csv_path: str,
    gpkg_path: str,
    lat_col: str = "latitude",
    lon_col: str = "longitude",
) -> dict:
    """
    Baca file CSV, buat geometry Point dari kolom koordinat,
    lalu simpan sebagai GeoPackage (.gpkg).

    Args:
        csv_path  : Path file CSV sumber.
        gpkg_path : Path file GeoPackage tujuan.
        lat_col   : Nama kolom latitude (default: "latitude").
        lon_col   : Nama kolom longitude (default: "longitude").

    Returns:
        {
            "epsg": int,
            "bbox": BoundingBox(left, bottom, right, top)
        }

    Raises:
        ValueError: Jika kolom lat/lon tidak ditemukan di CSV.
    """
    df = pd.read_csv(csv_path)

    # Validasi kolom koordinat
    missing = [c for c in [lat_col, lon_col] if c not in df.columns]
    if missing:
        raise ValueError(
            f"Kolom koordinat tidak ditemukan di CSV: {missing}. "
            f"Kolom yang tersedia: {list(df.columns)}"
        )

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[lon_col], df[lat_col]),
        crs="EPSG:4326",
    )

    gdf.to_file(gpkg_path, driver="GPKG")

    total_bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
    bbox = BoundingBox(
        left=total_bounds[0],
        bottom=total_bounds[1],
        right=total_bounds[2],
        top=total_bounds[3],
    )

    return {
        "epsg": 4326,
        "bbox": bbox,
    }
