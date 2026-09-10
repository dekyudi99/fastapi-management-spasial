from typing import List, Optional, Dict, Any
from config.geoserver_auth import get_geoserver_connection

geo = get_geoserver_connection()

def create_geoserver_layergroup(
    name: str,
    title: str,
    workspace: str,
    layers: List[str],
    mode: str = "single",
    abstract_text: Optional[str] = None,
    keywords: Optional[List[str]] = None
) -> bool:
    """
    Membuat Layer Group baru di GeoServer menggunakan geoserver-rest.
    :param name: Nama unik teknis layer group
    :param title: Judul display layer group
    :param workspace: Nama workspace di GeoServer (ws_name)
    :param layers: List nama geoserver_name dari layer yang digabungkan
    :param mode: 'single', 'named', 'container', 'eo'
    :param abstract_text: Deskripsi layer group
    :param keywords: List kata kunci
    """
    try:
        response = geo.create_layergroup(
            name=name,
            mode=mode or "single",
            title=title,
            abstract_text=abstract_text or "",
            layers=layers,
            workspace=workspace,
            keywords=keywords or []
        )
        return response
    except Exception as e:
        raise RuntimeError(f"Gagal membuat Layer Group di GeoServer: {str(e)}")

def add_layer_to_geoserver_group(
    layergroup_name: str,
    workspace: str,
    layer_name: str
) -> bool:
    """
    Menambahkan satu layer ke dalam layer group di GeoServer.
    """
    try:
        response = geo.add_layer_to_layergroup(
            layergroup_name=layergroup_name,
            layergroup_workspace=workspace,
            layer_name=layer_name,
            layer_workspace=workspace
        )
        return response
    except Exception as e:
        raise RuntimeError(f"Gagal menambahkan layer '{layer_name}' ke Layer Group '{layergroup_name}': {str(e)}")

def remove_layer_from_geoserver_group(
    layergroup_name: str,
    workspace: str,
    layer_name: str
) -> bool:
    """
    Menghapus satu layer dari layer group di GeoServer.
    """
    try:
        response = geo.remove_layer_from_layergroup(
            layergroup_name=layergroup_name,
            layergroup_workspace=workspace,
            layer_name=layer_name,
            layer_workspace=workspace
        )
        return response
    except Exception as e:
        raise RuntimeError(f"Gagal menghapus layer '{layer_name}' dari Layer Group '{layergroup_name}': {str(e)}")

def delete_geoserver_layergroup(
    layergroup_name: str,
    workspace: str
) -> bool:
    """
    Menghapus Layer Group dari GeoServer.
    """
    try:
        if hasattr(geo, "delete_layergroup"):
            return geo.delete_layergroup(layergroup_name, workspace=workspace)
        elif hasattr(geo, "delete_layer_group"):
            return geo.delete_layer_group(layergroup_name, workspace=workspace)
        return True
    except Exception as e:
        print(f"Peringatan saat menghapus layer group di GeoServer: {e}")
        return False

def get_geoserver_layergroup(
    layergroup_name: str,
    workspace: str
) -> Optional[Dict[str, Any]]:
    """
    Mendapatkan detail Layer Group dari GeoServer.
    """
    try:
        if hasattr(geo, "get_layergroup"):
            return geo.get_layergroup(layergroup_name, workspace=workspace)
        return None
    except Exception as e:
        print(f"Gagal mengambil data layer group dari GeoServer: {e}")
        return None
