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
    geoserver_url = os.getenv("GEOSERVER_URL", "http://geoserver:8080/geoserver")
    user = os.getenv("GEOSERVER_USER", "admin")
    pw = os.getenv("GEOSERVER_PASS", "rahasia")
    auth = HTTPBasicAuth(user, pw)
    
    style_url = f"{geoserver_url}/rest/styles/{style_name}"
    style_check = requests.get(f"{style_url}.json", auth=auth)
    
    if style_check.status_code == 404:
        create_res = requests.post(
            f"{geoserver_url}/rest/styles",
            headers={"Content-Type": "text/xml"},
            data=f"<style><name>{style_name}</name><filename>{style_name}.sld</filename></style>",
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


def assign_style_to_layer(workspace: str, layer_name: str, style_name: str) -> bool:
    geoserver_url = os.getenv("GEOSERVER_URL", "http://geoserver:8080/geoserver")
    user = os.getenv("GEOSERVER_USER", "admin")
    pw = os.getenv("GEOSERVER_PASS", "rahasia")
    auth = HTTPBasicAuth(user, pw)

    layer_xml = f"""
    <layer>
        <defaultStyle>
            <name>{style_name}</name>
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
