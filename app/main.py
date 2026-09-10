from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import info, layer, workspace, store, service, auth, coverage_store, project, api_key, endpoint_test, s2s, layer_group

import os

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