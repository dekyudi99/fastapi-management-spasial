import os
from dotenv import load_dotenv
from geo.Geoserver import Geoserver

load_dotenv()

def get_geoserver_connection():
    url = os.getenv("GEOSERVER_URL")
    user = os.getenv("GEOSERVER_USER")
    pw = os.getenv("GEOSERVER_PASS")
    return Geoserver(url, username=user, password=pw)