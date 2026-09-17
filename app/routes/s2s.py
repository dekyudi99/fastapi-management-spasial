"""
Router S2S (System-to-System / Service-to-Service).
Endpoint ini digunakan untuk integrasi antar sistem (contoh: FlowGIS / Laravel → Management Spatial).
Autentikasi menggunakan API Key (header: X-API-Key: agis_sk_...), bukan JWT/email-password.

Fungsi dan controller telah dimodularisasi ke dalam package `controller_s2s/`:
- `publish_controller.py`: Publikasi raster dari URL & multipart file upload, serta styling SLD.
- `layer_controller.py`: Manajemen layer (query daftar layer & hapus layer).
- `layer_group_controller.py`: CRUD Layer Groups (create, list, update, delete).
"""

from fastapi import APIRouter

from .controller_s2s.publish_controller import (
    router as publish_router,
    S2SPublishFromUrlRequest,
    _apply_layer_style,
    geo,
    RASTER_PATH,
)
from .controller_s2s.layer_controller import (
    router as layer_router,
)
from .controller_s2s.layer_group_controller import (
    router as layer_group_router,
    S2SCreateLayerGroupRequest,
    S2SUpdateLayerGroupRequest,
)

router = APIRouter(prefix="/s2s", tags=["System-to-System"])

router.include_router(publish_router)
router.include_router(layer_router)
router.include_router(layer_group_router)

__all__ = [
    "router",
    "publish_router",
    "layer_router",
    "layer_group_router",
    "S2SPublishFromUrlRequest",
    "S2SCreateLayerGroupRequest",
    "S2SUpdateLayerGroupRequest",
    "_apply_layer_style",
    "geo",
    "RASTER_PATH",
]
