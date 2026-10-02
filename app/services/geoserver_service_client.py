import os
import requests
from typing import Dict, Any, Optional, List
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("astragis.geoserver_client")

class GeoServerServiceClient:
    """
    Client SDK untuk berkomunikasi dengan geoserver-microservice.
    Menyediakan interface terpadu (DRY) untuk semua operasi GeoServer dari AstraGIS Core.
    Semua request diamankan menggunakan API Key (X-API-Key).
    """

    def __init__(self, service_url: Optional[str] = None, api_key: Optional[str] = None):
        self.service_url = (
            service_url or 
            os.getenv("GEOSERVER_MICROSERVICE_URL") or 
            "http://localhost:8005"
        ).rstrip("/")
        self.api_key = (
            api_key or 
            os.getenv("GEOSERVER_MICROSERVICE_API_KEY") or 
            os.getenv("API_KEY") or 
            "rahasia_s2s_geoserver_key_2026"
        )
        self.timeout = 60

    def get_active_primary_key(self) -> str:
        """
        Ambil kunci PRIMARY aktif langsung dari database lokal secara dinamis.
        Fallback ke environment variable jika tidak ditemukan.
        """
        try:
            from config.database import SessionLocal
            from models.api_key import ApiKey
            from models.users import Users
            db = SessionLocal()
            try:
                pk = db.query(ApiKey).filter(
                    ApiKey.key_prefix == "gsvc_pk_",
                    ApiKey.is_active == True,
                    ApiKey.api_key_secret.isnot(None)
                ).first()
                if pk and pk.api_key_secret:
                    return pk.api_key_secret
            finally:
                db.close()
        except Exception:
            pass
        return os.getenv("GEOSERVER_MICROSERVICE_API_KEY") or self.api_key

    def _url(self, path: str) -> str:
        clean_path = path.lstrip('/')
        if not clean_path.startswith("api/v1/"):
            clean_path = f"api/v1/{clean_path}"
        return f"{self.service_url}/{clean_path}"

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        current_key = self.get_active_primary_key()
        headers = {
            "X-API-Key": current_key
        }
        if extra:
            headers.update(extra)
        return headers

    def is_service_available(self) -> bool:
        try:
            res = requests.get(self._url("/health"), timeout=3)
            return res.status_code == 200
        except Exception:
            return False

    def create_workspace(self, workspace_name: str) -> bool:
        try:
            res = requests.post(
                self._url("/workspaces"),
                json={"workspace_name": workspace_name},
                headers=self._headers(),
                timeout=self.timeout
            )
            return res.status_code in (200, 201)
        except Exception as e:
            logger.error(f"Error creating workspace {workspace_name}: {e}")
            return False

    def delete_workspace(self, workspace_name: str, recurse: bool = True) -> bool:
        try:
            res = requests.delete(
                self._url(f"/workspaces/{workspace_name}?recurse={str(recurse).lower()}"),
                headers=self._headers(),
                timeout=self.timeout
            )
            return res.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Error deleting workspace {workspace_name}: {e}")
            return False

    def list_workspaces(self) -> List[Dict[str, Any]]:
        try:
            res = requests.get(self._url("/workspaces"), headers=self._headers(), timeout=self.timeout)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.error(f"Error listing workspaces: {e}")
        return []

    def publish_vector(
        self,
        workspace_name: str,
        layer_name: str,
        file_tuple: tuple,  # (filename, fileobj, content_type)
        table_name: Optional[str] = None,
        simplify_tolerance: Optional[float] = None
    ) -> Dict[str, Any]:
        data = {
            "workspace_name": workspace_name,
            "layer_name": layer_name
        }
        if table_name:
            data["table_name"] = table_name
        if simplify_tolerance is not None:
            data["simplify_tolerance"] = str(simplify_tolerance)

        files = {"file": file_tuple}
        res = requests.post(
            self._url("/layers/publish-vector"),
            data=data,
            files=files,
            headers=self._headers(),
            timeout=self.timeout
        )
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Microservice publish vector error: {res.text}")
        return res.json()

    def publish_raster(
        self,
        workspace_name: str,
        layer_name: str,
        file_tuple: tuple,  # (filename, fileobj, content_type)
        store_name: Optional[str] = None,
        style_config_json: Optional[str] = None
    ) -> Dict[str, Any]:
        data = {
            "workspace_name": workspace_name,
            "layer_name": layer_name
        }
        if store_name:
            data["store_name"] = store_name
        if style_config_json:
            data["style_config"] = style_config_json

        files = {"file": file_tuple}
        res = requests.post(
            self._url("/layers/publish-raster"),
            data=data,
            files=files,
            headers=self._headers(),
            timeout=self.timeout
        )
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Microservice publish raster error: {res.text}")
        return res.json()

    def delete_layer(self, workspace_name: str, layer_name: str, recurse: bool = True) -> bool:
        try:
            res = requests.delete(
                self._url(f"/layers/{workspace_name}/{layer_name}?recurse={str(recurse).lower()}"),
                headers=self._headers(),
                timeout=self.timeout
            )
            return res.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Error deleting layer {layer_name}: {e}")
            return False

    def get_layer(self, layer_name: str, workspace_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        try:
            url = self._url(f"/layers/{layer_name}")
            params = {"workspace": workspace_name} if workspace_name else {}
            res = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.error(f"Error fetching layer {layer_name}: {e}")
        return None

    def apply_style(
        self,
        workspace_name: str,
        layer_name: str,
        style_name: str,
        sld_xml: Optional[str] = None
    ) -> bool:
        res = requests.post(
            self._url("/styles/apply"),
            json={
                "workspace_name": workspace_name,
                "layer_name": layer_name,
                "style_name": style_name,
                "sld_xml": sld_xml
            },
            headers=self._headers(),
            timeout=self.timeout
        )
        return res.status_code == 200

    def delete_style(self, style_name: str, workspace_name: Optional[str] = None, recurse: bool = True) -> bool:
        try:
            url = self._url(f"/styles/{style_name}")
            params = {"workspace": workspace_name, "recurse": str(recurse).lower()} if workspace_name else {"recurse": str(recurse).lower()}
            res = requests.delete(url, params=params, headers=self._headers(), timeout=self.timeout)
            return res.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Error deleting style {style_name}: {e}")
            return False

    def get_wms_capabilities(self, workspace_name: Optional[str] = None) -> Dict[str, Any]:
        url = self._url("/wms/capabilities")
        params = {"workspace": workspace_name} if workspace_name else {}
        res = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout)
        if res.status_code == 200:
            return res.json()
        raise RuntimeError(f"Gagal mengambil WMS capabilities: {res.text}")

    def list_datastores(self, workspace_name: Optional[str] = None) -> List[Dict[str, Any]]:
        url = self._url("/stores")
        params = {"workspace": workspace_name} if workspace_name else {}
        res = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout)
        return res.json() if res.status_code == 200 else []

    def get_datastore(self, store_name: str, workspace_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        url = self._url(f"/stores/{workspace_name}/{store_name}" if workspace_name else f"/stores/{store_name}")
        res = requests.get(url, headers=self._headers(), timeout=self.timeout)
        return res.json() if res.status_code == 200 else None

    def list_coveragestores(self, workspace_name: Optional[str] = None) -> List[Dict[str, Any]]:
        url = self._url("/coverage-stores")
        params = {"workspace": workspace_name} if workspace_name else {}
        res = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout)
        return res.json() if res.status_code == 200 else []

    def get_coveragestore(self, store_name: str, workspace_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        url = self._url(f"/coverage-stores/{workspace_name}/{store_name}" if workspace_name else f"/coverage-stores/{store_name}")
        res = requests.get(url, headers=self._headers(), timeout=self.timeout)
        return res.json() if res.status_code == 200 else None

    def delete_coveragestore(self, workspace_name: str, store_name: str, recurse: bool = True) -> bool:
        try:
            res = requests.delete(
                self._url(f"/coverage-stores/{workspace_name}/{store_name}?recurse={str(recurse).lower()}"),
                headers=self._headers(),
                timeout=self.timeout
            )
            return res.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Error deleting coverage store {store_name}: {e}")
            return False

    def get_version(self) -> Dict[str, Any]:
        try:
            res = requests.get(self._url("/health"), headers=self._headers(), timeout=5)
            if res.status_code == 200:
                return {"version": "2.28.2", "microservice": "geoserver-microservice"}
        except Exception:
            pass
        return {"version": "unknown"}

    def get_status(self) -> Dict[str, Any]:
        try:
            res = requests.get(self._url("/health"), headers=self._headers(), timeout=5)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        return {"status": "unavailable"}

    # ==========================================
    # API Key Management (Microservice S2S)
    # ==========================================
    def create_api_key(
        self,
        name: str,
        key_type: str = "STANDARD",
        owner_info: Optional[str] = None
    ) -> Dict[str, Any]:
        payload = {
            "name": name,
            "key_type": key_type,
            "owner_info": owner_info
        }
        res = requests.post(
            self._url("/api-keys"),
            json=payload,
            headers=self._headers(),
            timeout=self.timeout
        )
        if res.status_code in (200, 201):
            return res.json()
        raise RuntimeError(f"Gagal membuat API key di microservice: {res.text}")

    def list_api_keys(self) -> List[Dict[str, Any]]:
        res = requests.get(self._url("/api-keys"), headers=self._headers(), timeout=self.timeout)
        if res.status_code == 200:
            return res.json()
        return []

    def deactivate_api_key(self, key_id: str) -> bool:
        try:
            res = requests.delete(self._url(f"/api-keys/{key_id}"), headers=self._headers(), timeout=self.timeout)
            return res.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Error deactivating API key {key_id}: {e}")
            return False

    def toggle_api_key(self, key_id: str) -> bool:
        try:
            res = requests.patch(self._url(f"/api-keys/{key_id}/toggle"), headers=self._headers(), timeout=self.timeout)
            return res.status_code == 200
        except Exception as e:
            logger.error(f"Error toggling API key {key_id}: {e}")
            return False

    def get_audit_logs(
        self,
        api_key_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        params = {"limit": limit, "offset": offset}
        if api_key_id:
            params["api_key_id"] = api_key_id
        if action:
            params["action"] = action
        res = requests.get(self._url("/logs"), params=params, headers=self._headers(), timeout=self.timeout)
        if res.status_code == 200:
            return res.json()
        return {"total": 0, "offset": offset, "limit": limit, "data": []}

    def get_key_usage(self, plain_key: str) -> Dict[str, Any]:
        try:
            res = requests.get(self._url("/api-keys/usage"), headers={"X-API-Key": plain_key}, timeout=5)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.warning(f"Could not fetch disk usage: {e}")
        return {
            "total_bytes": 0,
            "total_readable": "0 B",
            "raster_bytes": 0,
            "raster_readable": "0 B",
            "vector_bytes": 0,
            "vector_readable": "0 B",
            "total_layers": 0,
            "raster_count": 0,
            "vector_count": 0
        }

    def test_connection(self) -> Dict[str, Any]:
        try:
            res = requests.get(self._url("/health"), timeout=5)
            if res.status_code == 200:
                data = res.json()
                return {
                    "success": True,
                    "connected": bool(data.get("geoserver_connected")),
                    "service_status": data.get("status", "ok"),
                    "geoserver_version": data.get("geoserver_version"),
                    "latency_ms": data.get("latency_ms", 0),
                    "geoserver_url": data.get("geoserver_url"),
                    "service_url": self.service_url
                }
            return {"success": False, "connected": False, "error": f"HTTP {res.status_code}"}
        except Exception as e:
            return {"success": False, "connected": False, "error": str(e)}

geoserver_service_client = GeoServerServiceClient()

