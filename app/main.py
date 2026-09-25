from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import info, layer, workspace, store, service, auth, coverage_store, project, api_key, endpoint_test, s2s, layer_group

import os
from dotenv import load_dotenv

load_dotenv()

from config.database import Base, engine
from sqlalchemy import text
from models import email_otp, users

# Pastikan tabel baru dibuat dan kolom is_verified tersedia di PostgreSQL
try:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;"))
        try:
            srid_check = conn.execute(text("SELECT COUNT(*) FROM spatial_ref_sys WHERE srid = 4326;")).scalar()
            if not srid_check:
                conn.execute(text("""
                    INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text)
                    VALUES (
                        4326,
                        'EPSG',
                        4326,
                        'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]',
                        '+proj=longlat +datum=WGS84 +no_defs'
                    ) ON CONFLICT (srid) DO NOTHING;
                """))
        except Exception as e_srid:
            print(f"[PostGIS SRID Check] {e_srid}")
except Exception as e:
    print(f"[DB Auto-Migration] {e}")

app = FastAPI()

default_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "https://astragis.ikya.my.id",
    "https://flowgis.ikya.my.id",
]

env_cors = os.getenv("CORS_ORIGINS")
origins = [orig.strip() for orig in env_cors.split(",") if orig.strip()] if env_cors else default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(endpoint_test.router)
app.include_router(info.router)
app.include_router(auth.router)
app.include_router(project.router)
app.include_router(api_key.router)
app.include_router(workspace.router)
app.include_router(store.router)
app.include_router(coverage_store.router)
app.include_router(layer.router)
app.include_router(service.router)
app.include_router(s2s.router)
app.include_router(layer_group.router)