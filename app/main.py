from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import info, auth, api_key, admin, endpoint_test

import os
from dotenv import load_dotenv

load_dotenv()

from config.database import Base, engine
from sqlalchemy import text
from models import email_otp, users, api_key as api_key_model

# Pastikan migrasi kolom penting tersedia di PostgreSQL
try:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user';"))
        conn.execute(text("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS user_id BIGINT;"))
        conn.execute(text("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS microservice_key_id VARCHAR(100);"))
        conn.execute(text("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS key_prefix VARCHAR(20) DEFAULT 'gsvc_sk_';"))
        conn.execute(text("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS masked_key VARCHAR(50);"))
        conn.execute(text("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS api_key_secret VARCHAR(255);"))
        
        # Buat user admin default jika belum ada admin sama sekali
        has_admin = conn.execute(text("SELECT COUNT(*) FROM users WHERE role = 'admin';")).scalar()
        if not has_admin:
            # Upgrade user pertama menjadi admin jika ada, atau buat penanda
            conn.execute(text("UPDATE users SET role = 'admin' WHERE id = (SELECT min(id) FROM users);"))
            print("[Admin Init] Promoted earliest user to admin.")
except Exception as e:
    print(f"[DB Auto-Migration] {e}")

app = FastAPI(
    title="AstraGIS Core Management API",
    description="Layanan inti AstraGIS: Manajemen Pengguna dan API Key untuk GeoServer Microservice",
    version="2.0.0"
)

default_origins = [
    "http://localhost:5173",
    "http://localhost:5175",
    "http://localhost:3000",
    "https://voxagis.wefgis.com",
    "https://flowgis.ikya.my.id",
]

origins = default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active Routers
app.include_router(endpoint_test.router)
app.include_router(info.router)
app.include_router(auth.router)
app.include_router(api_key.router)
app.include_router(admin.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)