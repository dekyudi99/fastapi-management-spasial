import os
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger("astragis.geoserver_auth")

def get_geoserver_connection():
    """
    Fallback direct connection.
    Disarankan untuk menggunakan services.geoserver_service_client.geoserver_service_client
    yang telah terisolasi dan diamankan dengan API Key.
    """
    url = os.getenv("GEOSERVER_URL", "http://geoserver:8080/geoserver")
    user = os.getenv("GEOSERVER_USER", "admin")
    pw = os.getenv("GEOSERVER_PASS", "rahasia")
    try:
        from geo.Geoserver import Geoserver
        return Geoserver(url, username=user, password=pw)
    except Exception as e:
        logger.warning(f"Could not connect directly to GeoServer: {e}")
        return None