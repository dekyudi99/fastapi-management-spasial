import os
import re
import zipfile
import tempfile
import xml.etree.ElementTree as ET
import requests
from requests.auth import HTTPBasicAuth
from typing import Tuple, Dict, Any, Optional
import geopandas as gpd
import pandas as pd
from shapely.geometry import (
    box, Point, LineString, Polygon,
    MultiPolygon, MultiLineString, MultiPoint, GeometryCollection
)
from shapely.validation import make_valid
from collections import namedtuple
from config.database import engine
from services.sld_to_layer import generate_vector_sld, apply_sld_to_layer

# Tipe yang sama dengan rasterio.bounds agar konsisten dengan handler TIFF
BoundingBox = namedtuple("BoundingBox", ["left", "bottom", "right", "top"])


def _parse_kml_xml(kml_path_or_content) -> gpd.GeoDataFrame:
    """
    Fallback parser native untuk berkas KML menggunakan xml.etree.ElementTree dan shapely.
    Mendukung Placemark Point, LineString, Polygon, MultiGeometry, serta ExtendedData.
    """
    if isinstance(kml_path_or_content, str) and os.path.exists(kml_path_or_content):
        tree = ET.parse(kml_path_or_content)
        root = tree.getroot()
    elif isinstance(kml_path_or_content, bytes):
        root = ET.fromstring(kml_path_or_content)
    else:
        root = ET.fromstring(str(kml_path_or_content))

    def _strip_ns(tag):
        return tag.split('}')[-1] if '}' in tag else tag

    def _parse_coords(coord_text):
        if not coord_text:
            return []
        # Gunakan regex untuk mengekstrak pasangan (lon, lat) dengan cepat, kebal spasi/newline
        matches = re.findall(r'([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*,\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)', coord_text)
        coords = []
        for x, y in matches:
            try:
                coords.append((float(x), float(y)))
            except (ValueError, TypeError):
                continue
        return coords

    def _parse_geom_node(node):
        tag = _strip_ns(node.tag)
        if tag == 'Point':
            for child in node:
                if _strip_ns(child.tag) == 'coordinates':
                    coords = _parse_coords(child.text)
                    if coords:
                        return Point(coords[0])
        elif tag == 'LineString':
            for child in node:
                if _strip_ns(child.tag) == 'coordinates':
                    coords = _parse_coords(child.text)
                    if len(coords) >= 2:
                        return LineString(coords)
        elif tag == 'Polygon':
            exterior = []
            interiors = []
            for child in node:
                c_tag = _strip_ns(child.tag)
                if c_tag == 'outerBoundaryIs':
                    for sub in child:
                        for s in sub:
                            if _strip_ns(s.tag) == 'coordinates':
                                exterior = _parse_coords(s.text)
                elif c_tag == 'innerBoundaryIs':
                    for sub in child:
                        for s in sub:
                            if _strip_ns(s.tag) == 'coordinates':
                                in_coords = _parse_coords(s.text)
                                if in_coords:
                                    interiors.append(in_coords)
            if exterior and len(exterior) >= 3:
                # Pastikan ring tertutup
                if exterior[0] != exterior[-1]:
                    exterior.append(exterior[0])
                clean_interiors = []
                for hole in interiors:
                    if len(hole) >= 3:
                        if hole[0] != hole[-1]:
                            hole.append(hole[0])
                        clean_interiors.append(hole)
                try:
                    poly = Polygon(exterior, clean_interiors)
                    return poly
                except Exception:
                    pass
        elif tag == 'MultiGeometry':
            sub_geoms = []
            for child in node:
                g = _parse_geom_node(child)
                if g:
                    sub_geoms.append(g)
            if sub_geoms:
                first_type = sub_geoms[0].geom_type
                if all(g.geom_type == first_type for g in sub_geoms):
                    if first_type in ('Polygon', 'MultiPolygon'):
                        flat_polys = []
                        for item in sub_geoms:
                            if item.geom_type == 'Polygon':
                                flat_polys.append(item)
                            elif item.geom_type == 'MultiPolygon':
                                flat_polys.extend(item.geoms)
                        return MultiPolygon(flat_polys)
                    elif first_type in ('LineString', 'MultiLineString'):
                        flat_lines = []
                        for item in sub_geoms:
                            if item.geom_type == 'LineString':
                                flat_lines.append(item)
                            elif item.geom_type == 'MultiLineString':
                                flat_lines.extend(item.geoms)
                        return MultiLineString(flat_lines)
                    elif first_type in ('Point', 'MultiPoint'):
                        flat_pts = []
                        for item in sub_geoms:
                            if item.geom_type == 'Point':
                                flat_pts.append(item)
                            elif item.geom_type == 'MultiPoint':
                                flat_pts.extend(item.geoms)
                        return MultiPoint(flat_pts)
                return GeometryCollection(sub_geoms)
        return None

    features = []
    # Cari semua elemen Placemark di seluruh hierarki XML
    for elem in root.iter():
        if _strip_ns(elem.tag) == 'Placemark':
            name = None
            description = None
            geom = None
            props = {}

            for child in elem:
                c_tag = _strip_ns(child.tag)
                if c_tag == 'name':
                    name = (child.text or '').strip()
                elif c_tag == 'description':
                    description = (child.text or '').strip()
                elif c_tag in ('Point', 'LineString', 'Polygon', 'MultiGeometry'):
                    geom = _parse_geom_node(child)
                elif c_tag == 'ExtendedData':
                    for ext_elem in child.iter():
                        ext_tag = _strip_ns(ext_elem.tag)
                        if ext_tag == 'Data':
                            d_name = ext_elem.get('name')
                            if d_name:
                                for v in ext_elem:
                                    if _strip_ns(v.tag) == 'value':
                                        props[d_name] = v.text
                        elif ext_tag == 'SimpleData':
                            s_name = ext_elem.get('name')
                            if s_name:
                                props[s_name] = ext_elem.text

            if geom:
                data_row = {'name': name, 'description': description, **props, 'geometry': geom}
                features.append(data_row)

    if not features:
        raise ValueError("Tidak ditemukan elemen geometri (Point, LineString, Polygon) yang valid di dalam berkas KML.")

    gdf = gpd.GeoDataFrame(features, crs="EPSG:4326")
    return gdf


def _read_kml_to_gdf(kml_path: str) -> gpd.GeoDataFrame:
    """
    Membaca file KML menggunakan parser native XML terlebih dahulu (sangat cepat, <100ms, tanpa timeout jaringan GDAL),
    dengan fallback ke Fiona/Pyogrio jika ada struktur KML yang tidak didukung parser native.
    """
    # 1. Coba parser XML native terlebih dahulu (instan, < 100ms, bebas delay jaringan GDAL)
    try:
        gdf = _parse_kml_xml(kml_path)
        if not gdf.empty:
            return gdf
    except Exception:
        pass

    # 2. Fallback: Coba menggunakan Fiona dengan mengaktifkan driver KML
    try:
        # pyrefly: ignore [missing-import]
        import fiona
        if 'KML' not in fiona.supported_drivers:
            fiona.supported_drivers['KML'] = 'rw'
        if 'LIBKML' not in fiona.supported_drivers:
            fiona.supported_drivers['LIBKML'] = 'rw'

        layers = fiona.listlayers(kml_path)
        if layers:
            gdfs = []
            for layer in layers:
                try:
                    df = gpd.read_file(kml_path, driver='KML', layer=layer)
                    if not df.empty:
                        gdfs.append(df)
                except Exception:
                    pass
            if gdfs:
                combined = pd.concat(gdfs, ignore_index=True)
                if not isinstance(combined, gpd.GeoDataFrame):
                    combined = gpd.GeoDataFrame(combined, crs="EPSG:4326")
                return combined
    except Exception:
        pass

    # 3. Fallback: Coba pyogrio atau read_file langsung
    try:
        gdf = gpd.read_file(kml_path, engine="pyogrio")
        if not gdf.empty:
            return gdf
    except Exception:
        pass

    try:
        gdf = gpd.read_file(kml_path, driver="KML")
        if not gdf.empty:
            return gdf
    except Exception:
        pass

    raise ValueError("Tidak dapat membaca data geometri yang valid dari berkas KML.")


def _read_kmz_to_gdf(kmz_path: str) -> gpd.GeoDataFrame:
    """
    Membaca berkas KMZ (arsip zip berisi KML) menjadi GeoDataFrame secara instan.
    Membaca langsung konten doc.kml dari memori tanpa ekstraksi disk dan tanpa timeout jaringan GDAL.
    """
    with zipfile.ZipFile(kmz_path, 'r') as z:
        kml_files = [f for f in z.namelist() if f.lower().endswith('.kml')]
        if not kml_files:
            raise ValueError("Tidak ditemukan file .kml di dalam arsip KMZ.")

        # Prioritaskan doc.kml jika ada
        target_kml = next((f for f in kml_files if os.path.basename(f).lower() == 'doc.kml'), kml_files[0])

        # 1. Coba baca langsung bytes target KML dari zip ke native parser (SUPER CEPAT, 10-50ms)
        try:
            kml_bytes = z.read(target_kml)
            gdf = _parse_kml_xml(kml_bytes)
            if not gdf.empty:
                return gdf
        except Exception:
            pass

        # 2. Jika KMZ memiliki beberapa file KML, coba ekstrak dari memori untuk setiap berkas
        if len(kml_files) > 1:
            all_parts = []
            for k_file in kml_files:
                try:
                    k_bytes = z.read(k_file)
                    p_gdf = _parse_kml_xml(k_bytes)
                    if not p_gdf.empty:
                        all_parts.append(p_gdf)
                except Exception:
                    continue
            if all_parts:
                combined = pd.concat(all_parts, ignore_index=True)
                if not isinstance(combined, gpd.GeoDataFrame):
                    combined = gpd.GeoDataFrame(combined, crs="EPSG:4326")
                return combined

        # 3. Fallback: ekstrak ke temp directory lalu coba via _read_kml_to_gdf
        with tempfile.TemporaryDirectory() as tmp_dir:
            extracted_path = z.extract(target_kml, tmp_dir)
            return _read_kml_to_gdf(extracted_path)


def get_vector_metadata(file_path: str) -> dict:
    """
    Baca file vector (GeoJSON, GeoPackage, Shapefile, KML, KMZ, CSV)
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
    Membaca berbagai format file vektor (GeoJSON, Shapefile .zip, .shp, .gpkg, .csv, .kml, .kmz)
    menjadi GeoDataFrame terstandarisasi dengan CRS EPSG:4326.
    Returns: (gdf, detected_format)
    """
    ext = os.path.splitext(file_path)[1].lower()

    def _read_fast(path):
        try:
            return gpd.read_file(path, engine="pyogrio")
        except Exception:
            return gpd.read_file(path)

    if ext in ('.geojson', '.json'):
        gdf = _read_fast(file_path)
        format_name = "GeoJSON"
    elif ext == '.zip':
        # Dukung ESRI Shapefile yang dikompresi dalam berkas .zip
        gdf = _read_fast(f"zip://{file_path}")
        format_name = "Shapefile"
    elif ext == '.shp':
        gdf = _read_fast(file_path)
        format_name = "Shapefile"
    elif ext == '.gpkg':
        gdf = _read_fast(file_path)
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
    elif ext == '.kml':
        gdf = _read_kml_to_gdf(file_path)
        format_name = "KML"
    elif ext == '.kmz':
        gdf = _read_kmz_to_gdf(file_path)
        format_name = "KMZ"
    else:
        raise ValueError(f"Format berkas '{ext}' tidak didukung sebagai data vektor.")

    # Standardisasi CRS ke EPSG:4326 (WGS84)
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    # Sanitize nama kolom agar aman untuk SQL PostGIS (hindari karakter khusus & spasi)
    clean_cols = {}
    for col in gdf.columns:
        if col != 'geometry':
            c_name = re.sub(r'[^a-zA-Z0-9_]', '_', str(col)).strip('_').lower()
            if not c_name or c_name in ('user', 'order', 'table', 'select', 'where', 'group', 'check'):
                c_name = f"attr_{c_name}"
            clean_cols[col] = c_name
    gdf = gdf.rename(columns=clean_cols)

    # Validasi geometri agar tidak ada invalid polygon (self-intersection)
    if 'geometry' in gdf.columns:
        gdf['geometry'] = gdf['geometry'].apply(lambda g: make_valid(g) if g is not None and not g.is_empty else g)

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

    # Hitung jumlah koordinat secara instan (C-vectorized via Shapely)
    try:
        import shapely
        total_vertices_before = int(shapely.get_num_coordinates(gdf.geometry.values).sum())
    except Exception:
        total_vertices_before = sum(count_vertices(g) for g in gdf.geometry)

    # Deteksi tipe geometri dominan
    geom_types = set(gdf.geom_type.dropna().unique())
    is_point_only = all(gt in ('Point', 'MultiPoint') for gt in geom_types)

    # Hanya jalankan simplifikasi koordinat jika terdapat geometri Polygon atau Line
    if not is_point_only and tolerance > 0:
        # Douglas-Peucker dengan jaminan topologi
        simplified_geoms = gdf.geometry.simplify(tolerance=tolerance, preserve_topology=preserve_topology)

        # Perbaiki poligon yang anomali secara selektif (hanya yang tidak valid)
        invalid_mask = ~simplified_geoms.is_valid & ~simplified_geoms.is_empty & simplified_geoms.notna()
        if invalid_mask.any():
            def safe_valid(g):
                if g is None or g.is_empty or g.is_valid:
                    return g
                try:
                    return make_valid(g)
                except Exception:
                    return g.buffer(0)
            simplified_geoms.loc[invalid_mask] = simplified_geoms.loc[invalid_mask].apply(safe_valid)

        gdf = gdf.copy()
        gdf['geometry'] = simplified_geoms

    try:
        import shapely
        total_vertices_after = int(shapely.get_num_coordinates(gdf.geometry.values).sum())
    except Exception:
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
    res = requests.get(check_url, auth=auth, timeout=15)
    if res.status_code == 200:
        return True

    # Jika workspace adalah 'geosocial', periksa apakah 'postgis_geosocial' sudah ada
    if workspace == "geosocial":
        res_geo = requests.get(f"{geoserver_url}/rest/workspaces/{workspace}/datastores/postgis_geosocial.json", auth=auth, timeout=15)
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
        auth=auth,
        timeout=20
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

    # 1. Simpan tabel ke PostGIS via SQLAlchemy engine (dengan streaming chunk agar stabil)
    gdf.to_postgis(table_name, engine, schema="public", if_exists="replace", index=False, chunksize=1000)

    # 2. Pastikan datastore siap
    # Jika workspace adalah geosocial, prioritaskan nama datastore 'postgis_geosocial'
    actual_store = "postgis_geosocial" if workspace_name == "geosocial" else store_name
    ensure_postgis_datastore(workspace_name, actual_store)

    # 3. Publikasikan FeatureType ke GeoServer
    ft_payload = {
        "featureType": {
            "name": table_name,
            "nativeName": table_name,
            "title": table_name,
            "srs": "EPSG:4326",
            "enabled": True
        }
    }

    ft_url = f"{geoserver_url}/rest/workspaces/{workspace_name}/datastores/{actual_store}/featuretypes"
    res = requests.post(ft_url, json=ft_payload, auth=auth, timeout=30)

    if res.status_code not in (200, 201):
        # Jika sudah ada, coba update atau periksa apakah FeatureType sudah terbit
        check_ft = requests.get(f"{ft_url}/{table_name}.json", auth=auth, timeout=15)
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
