import os
import requests
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Optional

def generate_raster_sld(style_name: str, color_entries: List[Dict], style_type: str = "values") -> str:
    """
    Generate SLD 1.0.0 XML untuk Raster Layer.
    style_type: 'values' (kategori diskrit), 'intervals' (rentang kelas), atau 'ramp' (gradien mulus)
    """
    entries_xml = ""
    for entry in color_entries:
        q = entry.get("quantity", 0)
        c = entry.get("color", "#000000")
        op = entry.get("opacity", 1.0)
        lbl = entry.get("label", f"Class {q}")
        entries_xml += f'              <ColorMapEntry color="{c}" quantity="{q}" opacity="{op}" label="{lbl}"/>\n'

    sld_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0" 
    xmlns="http://www.opengis.net/sld"
    xmlns:ogc="http://www.opengis.net/ogc" 
    xmlns:xlink="http://www.w3.org/1999/xlink" 
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <NamedLayer>
    <Name>{style_name}</Name>
    <UserStyle>
      <Title>{style_name}</Title>
      <FeatureTypeStyle>
        <Rule>
          <RasterSymbolizer>
            <ColorMap type="{style_type}">
{entries_xml}            </ColorMap>
          </RasterSymbolizer>
        </Rule>
      </FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>
"""
    return sld_xml


def create_or_update_style(style_name: str, sld_xml: str) -> bool:
    geoserver_url = os.getenv("GEOSERVER_URL").rstrip("/")
    user = os.getenv("GEOSERVER_USER")
    pw = os.getenv("GEOSERVER_PASS")
    auth = HTTPBasicAuth(user, pw)
    
    # Bersihkan nama style dari ekstensi agar GeoServer tidak keliru membaca format URL
    clean_name = os.path.splitext(style_name)[0].replace('.', '_')
    style_url = f"{geoserver_url}/rest/styles/{clean_name}"
    style_check = requests.get(f"{style_url}.json", auth=auth)
    
    if style_check.status_code == 404:
        create_res = requests.post(
            f"{geoserver_url}/rest/styles",
            headers={"Content-Type": "text/xml"},
            data=f"<style><name>{clean_name}</name><filename>{clean_name}.sld</filename></style>",
            auth=auth
        )
        if create_res.status_code not in (200, 201):
            raise Exception(f"Gagal membuat style di GeoServer: {create_res.text}")

    upload_res = requests.put(
        style_url,
        headers={"Content-Type": "application/vnd.ogc.sld+xml"},
        data=sld_xml.encode('utf-8'),
        auth=auth
    )
    if upload_res.status_code not in (200, 201):
        raise Exception(f"Gagal mengunggah SLD ke GeoServer: {upload_res.text}")

    return True


def style_exists_in_geoserver(style_name: str) -> bool:
    """Cek apakah style tertentu sudah ada di GeoServer."""
    clean_name = os.path.splitext(style_name)[0].replace('.', '_')
    geoserver_url = os.getenv("GEOSERVER_URL").rstrip("/")
    user = os.getenv("GEOSERVER_USER")
    pw = os.getenv("GEOSERVER_PASS")
    auth = HTTPBasicAuth(user, pw)
    res = requests.get(f"{geoserver_url}/rest/styles/{clean_name}.json", auth=auth)
    return res.status_code == 200


def assign_style_to_layer(workspace: str, layer_name: str, style_name: str) -> bool:
    geoserver_url = os.getenv("GEOSERVER_URL").rstrip("/")
    user = os.getenv("GEOSERVER_USER")
    pw = os.getenv("GEOSERVER_PASS")
    auth = HTTPBasicAuth(user, pw)

    clean_style = os.path.splitext(style_name)[0].replace('.', '_')

    # Verifikasi style ada di GeoServer sebelum assign
    if not style_exists_in_geoserver(clean_style):
        raise Exception(f"Style '{clean_style}' tidak ditemukan di GeoServer.")

    # 1. Coba assign via JSON ke endpoint workspace (kebal dari nama layer yang mengandung ekstensi .tif)
    try:
        ws_assign_url = f"{geoserver_url}/rest/workspaces/{workspace}/layers/{layer_name}.json"
        assign_res = requests.put(
            ws_assign_url,
            headers={"Content-Type": "application/json"},
            json={"layer": {"defaultStyle": {"name": clean_style}}},
            auth=auth
        )
        if assign_res.status_code in (200, 201):
            return True
    except Exception:
        pass

    # 2. Fallback via XML ke global layers endpoint
    layer_xml = f"""
    <layer>
        <defaultStyle>
            <name>{clean_style}</name>
        </defaultStyle>
    </layer>
    """
    assign_res = requests.put(
        f"{geoserver_url}/rest/layers/{workspace}:{layer_name}",
        headers={"Content-Type": "text/xml"},
        data=layer_xml.strip(),
        auth=auth
    )
    
    if assign_res.status_code not in (200, 201):
        raise Exception(f"Gagal mengaitkan style ke layer {layer_name}: {assign_res.text}")

    return True


def apply_sld_to_layer(workspace: str, layer_name: str, style_name: str, sld_xml: Optional[str] = None) -> bool:
    """
    1. Jika sld_xml disertakan, buat/perbarui style di GeoServer.
    2. Kaitkan style tersebut sebagai defaultStyle ke layer.
    """
    if sld_xml:
        create_or_update_style(style_name, sld_xml)
    return assign_style_to_layer(workspace, layer_name, style_name)


def generate_vector_sld(
    style_name: str,
    geom_type: str = "polygon",
    fill_color: str = "#0d9488",
    stroke_color: str = "#0f766e",
    stroke_width: float = 1.5,
    fill_opacity: float = 0.45
) -> str:
    """
    Generate SLD 1.0.0 XML untuk Vector Layer (Polygon, Line, Point).
    """
    geom_type_lower = (geom_type or "polygon").lower()
    if "polygon" in geom_type_lower:
        symbolizer_xml = f"""            <PolygonSymbolizer>
              <Fill>
                <CssParameter name="fill">{fill_color}</CssParameter>
                <CssParameter name="fill-opacity">{fill_opacity}</CssParameter>
              </Fill>
              <Stroke>
                <CssParameter name="stroke">{stroke_color}</CssParameter>
                <CssParameter name="stroke-width">{stroke_width}</CssParameter>
              </Stroke>
            </PolygonSymbolizer>"""
    elif "line" in geom_type_lower or "string" in geom_type_lower:
        symbolizer_xml = f"""            <LineSymbolizer>
              <Stroke>
                <CssParameter name="stroke">{stroke_color}</CssParameter>
                <CssParameter name="stroke-width">{stroke_width}</CssParameter>
              </Stroke>
            </LineSymbolizer>"""
    else:
        # Point
        symbolizer_xml = f"""            <PointSymbolizer>
              <Graphic>
                <Mark>
                  <WellKnownName>circle</WellKnownName>
                  <Fill>
                    <CssParameter name="fill">{fill_color}</CssParameter>
                  </Fill>
                  <Stroke>
                    <CssParameter name="stroke">{stroke_color}</CssParameter>
                    <CssParameter name="stroke-width">{stroke_width}</CssParameter>
                  </Stroke>
                </Mark>
                <Size>8</Size>
              </Graphic>
            </PointSymbolizer>"""

    sld_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0" 
    xmlns="http://www.opengis.net/sld"
    xmlns:ogc="http://www.opengis.net/ogc" 
    xmlns:xlink="http://www.w3.org/1999/xlink" 
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <NamedLayer>
    <Name>{style_name}</Name>
    <UserStyle>
      <Title>{style_name}</Title>
      <FeatureTypeStyle>
        <Rule>
{symbolizer_xml}
        </Rule>
      </FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>
"""
    return sld_xml

