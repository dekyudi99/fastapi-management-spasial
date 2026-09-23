import os
import requests
from requests.auth import HTTPBasicAuth
from typing import Tuple, Dict, Any, Optional
import geopandas as gpd
import pandas as pd
from shapely.geometry import box
from shapely.validation import make_valid
from collections import namedtuple
from config.database import engine
from services.sld_to_layer import generate_vector_sld, apply_sld_to_layer

# Tipe yang sama dengan rasterio.bounds agar konsisten dengan handler TIFF
BoundingBox = namedtuple("BoundingBox", ["left", "bottom", "right", "top"])


def get_vector_metadata(file_path: str) -> dict:
    """
    Baca file vector (GeoJSON, GeoPackage, atau direktori Shapefile)
    menggunakan geopandas dan kembalikan epsg serta bbox.
    """
    gdf, _ = read_vector_file_to_gdf(file_path)

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)

    epsg = gdf.crs.to_epsg() or 4326

    total_bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
    bbox = BoundingBox(
        left=float(total_bounds[0]),
        bottom=float(total_bounds[1]),
        right=float(total_bounds[2]),
        top=float(total_bounds[3]),
    )

    return {
        "epsg": epsg,
        "bbox": bbox,
    }


def read_vector_file_to_gdf(file_path: str) -> Tuple[gpd.GeoDataFrame, str]:
    """
    Membaca berbagai format file vektor (GeoJSON, Shapefile .zip, .shp, .gpkg, .csv)
    menjadi GeoDataFrame terstandarisasi dengan CRS EPSG:4326.
    Returns: (gdf, detected_format)
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext in ('.geojson', '.json'):
        gdf = gpd.read_file(file_path)
        format_name = "GeoJSON"
    elif ext == '.zip':
        # Dukung ESRI Shapefile yang dikompresi dalam berkas .zip
        gdf = gpd.read_file(f"zip://{file_path}")
        format_name = "Shapefile"
    elif ext == '.shp':
        gdf = gpd.read_file(file_path)
        format_name = "Shapefile"
    elif ext == '.gpkg':
        gdf = gpd.read_file(file_path)
        format_name = "GeoPackage"
    elif ext == '.csv':
        df = pd.read_csv(file_path)
        cols = {c.lower().strip(): c for c in df.columns}
        lat_candidates = ['lat', 'latitude', 'y', 'lintang', 'ycoord']
        lon_candidates = ['lon', 'long', 'lng', 'longitude', 'x', 'bujur', 'xcoord']
        lat_col = next((cols[c] for c in lat_candidates if c in cols), None)
        lon_col = next((cols[c] for c in lon_candidates if c in cols), None)
        if not lat_col or not lon_col:
            raise ValueError(
                f"Kolom koordinat Latitude/Longitude tidak ditemukan pada CSV. Kolom tersedia: {list(df.columns)}"
            )
        # Filter row dengan nilai koordinat valid
        df = df.dropna(subset=[lat_col, lon_col])
        gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon_col], df[lat_col]), crs="EPSG:4326")
        format_name = "CSV"
    else:
        raise ValueError(f"Format berkas '{ext}' tidak didukung sebagai data vektor.")

    # Standardisasi CRS ke EPSG:4326 (WGS84)
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    return gdf, format_name


def simplify_vector_gdf(
    gdf: gpd.GeoDataFrame,
    tolerance: float = 0.00005,
    preserve_topology: bool = True
) -> Tuple[gpd.GeoDataFrame, dict]:
    """
    Sederhanakan titik dan garis pada GeoDataFrame menggunakan algoritma Douglas-Peucker
    dengan preserve_topology=True agar bentuk tidak berubah dan topologi poligon/garis tetap valid.

    :param tolerance: Toleransi dalam derajat koordinat (0.00005 deg ~ 5 meter).
    :return: (simplified_gdf, statistics_dict)
    """
    def count_vertices(geom):
        if geom is None or geom.is_empty:
            return 0
        g_type = geom.geom_type
        if g_type in ('Polygon', 'LinearRing'):
            return len(geom.exterior.coords) + sum(len(interior.coords) for interior in geom.interiors)
        elif g_type == 'MultiPolygon':
            return sum(len(p.exterior.coords) + sum(len(interior.coords) for interior in p.interiors) for p in geom.geoms)
        elif g_type in ('LineString', 'MultiPoint'):
            return len(geom.coords)
        elif g_type == 'MultiLineString':
            return sum(len(line.coords) for line in geom.geoms)
        elif g_type == 'Point':
            return 1
        elif g_type == 'GeometryCollection':
            return sum(count_vertices(part) for part in geom.geoms)
        return 0

    total_vertices_before = sum(count_vertices(g) for g in gdf.geometry)

    # Deteksi tipe geometri dominan
    geom_types = set(gdf.geom_type.dropna().unique())
    is_point_only = all(gt in ('Point', 'MultiPoint') for gt in geom_types)

    # Hanya jalankan simplifikasi koordinat jika terdapat geometri Polygon atau Line
    if not is_point_only and tolerance > 0:
        # Douglas-Peucker dengan jaminan topologi
        simplified_geoms = gdf.geometry.simplify(tolerance=tolerance, preserve_topology=preserve_topology)

        # Perbaiki setiap poligon yang mungkin anomali pasca-simplifikasi
        def safe_valid(g):
            if g is None or g.is_empty or g.is_valid:
                return g
            try:
                return make_valid(g)
            except Exception:
                return g.buffer(0)

        gdf = gdf.copy()
        gdf['geometry'] = simplified_geoms.apply(safe_valid)

    total_vertices_after = sum(count_vertices(g) for g in gdf.geometry)
    reduction_pct = 0.0
    if total_vertices_before > 0:
        reduction_pct = round((1.0 - (total_vertices_after / total_vertices_before)) * 100.0, 1)

    dominant_geom = next(iter(geom_types), "Polygon") if geom_types else "Polygon"

    stats = {
        "vertices_before": total_vertices_before,
        "vertices_after": total_vertices_after,
        "reduction_percentage": max(0.0, reduction_pct),
        "tolerance_used": tolerance,
        "feature_count": len(gdf),
        "geometry_type": dominant_geom,
    }

    return gdf, stats


def ensure_postgis_datastore(workspace: str, store_name: str = "postgis_store") -> bool:
    """
    Pastikan datastore PostGIS tersedia pada workspace di GeoServer.
    Jika belum ada, buat koneksi datastore otomatis ke spasial_db.
    """
    geoserver_url = os.getenv("GEOSERVER_URL").rstrip("/")
    user = os.getenv("GEOSERVER_USER")
    pw = os.getenv("GEOSERVER_PASS")
    auth = HTTPBasicAuth(user, pw)

    check_url = f"{geoserver_url}/rest/workspaces/{workspace}/datastores/{store_name}.json"
    res = requests.get(check_url, auth=auth)
    if res.status_code == 200:
        return True

    # Jika workspace adalah 'geosocial', periksa apakah 'postgis_geosocial' sudah ada
    if workspace == "geosocial":
        res_geo = requests.get(f"{geoserver_url}/rest/workspaces/{workspace}/datastores/postgis_geosocial.json", auth=auth)
        if res_geo.status_code == 200:
            return True

    # Buat datastore baru menghubungkan ke PostGIS
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT")
    db_name = os.getenv("DB_NAME")
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASSWORD")

    payload = {
        "dataStore": {
            "name": store_name,
            "connectionParameters": {
                "host": db_host,
                "port": db_port,
                "database": db_name,
                "user": db_user,
                "passwd": db_pass,
                "dbtype": "postgis",
                "schema": "public",
                "Expose primary keys": "true",
                "validate connections": "true"
            }
        }
    }

    create_res = requests.post(
        f"{geoserver_url}/rest/workspaces/{workspace}/datastores",
        json=payload,
        auth=auth
    )
    return create_res.status_code in (200, 201)


def publish_vector_to_geoserver_postgis(
    gdf: gpd.GeoDataFrame,
    table_name: str,
    workspace_name: str,
    title: str,
    store_name: str = "postgis_store",
    geom_type: str = "polygon"
) -> bool:
    """
    1. Simpan GeoDataFrame yang sudah disederhanakan ke PostGIS (spasial_db.public.table_name).
    2. Pastikan datastore PostGIS aktif di GeoServer.
    3. Publikasikan FeatureType ke GeoServer.
    4. Buat dan kaitkan style SLD vector bawaan.
    """
    geoserver_url = os.getenv("GEOSERVER_URL").rstrip("/")
    user = os.getenv("GEOSERVER_USER")
    pw = os.getenv("GEOSERVER_PASS")
    auth = HTTPBasicAuth(user, pw)

    # 1. Simpan tabel ke PostGIS via SQLAlchemy engine
    gdf.to_postgis(table_name, engine, schema="public", if_exists="replace", index=False)

    # 2. Pastikan datastore siap
    # Jika workspace adalah geosocial, prioritaskan nama datastore 'postgis_geosocial'
    actual_store = "postgis_geosocial" if workspace_name == "geosocial" else store_name
    ensure_postgis_datastore(workspace_name, actual_store)

    # 3. Publikasikan FeatureType ke GeoServer
    ft_payload = {
        "featureType": {
            "name": table_name,
            "nativeName": table_name,
            "title": title,
            "srs": "EPSG:4326",
            "enabled": True
        }
    }

    ft_url = f"{geoserver_url}/rest/workspaces/{workspace_name}/datastores/{actual_store}/featuretypes"
    res = requests.post(ft_url, json=ft_payload, auth=auth)

    if res.status_code not in (200, 201):
        # Jika sudah ada, coba update atau periksa apakah FeatureType sudah terbit
        check_ft = requests.get(f"{ft_url}/{table_name}.json", auth=auth)
        if check_ft.status_code != 200:
            raise RuntimeError(f"Gagal menerbitkan FeatureType di GeoServer: {res.text}")

    # 4. Generate & Pasang SLD Style bawaan
    style_name = f"style_{table_name}"
    sld_xml = generate_vector_sld(style_name=style_name, geom_type=geom_type)
    try:
        apply_sld_to_layer(
            workspace=workspace_name,
            layer_name=table_name,
            style_name=style_name,
            sld_xml=sld_xml
        )
    except Exception as se:
        print(f"Peringatan: Gagal mengaitkan style vektor: {se}")

    return True
