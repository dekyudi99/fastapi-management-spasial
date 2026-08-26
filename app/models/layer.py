from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text
from geoalchemy2 import Geometry
from config.database import Base
from services.timesatampz_service import TimestampMixin

class Layer(TimestampMixin, Base):
    __tablename__ = "layers"
    id = Column(Integer, primary_key=True)

    workspace_id = Column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False
    )

    # Nama yang ditampilkan ke user
    name = Column(String(100), nullable=False)

    # Nama asli di GeoServer (unik)
    geoserver_name = Column(String(150), unique=True, nullable=False)

    description = Column(Text)

    # raster / vector
    layer_type = Column(String(20))

    # GeoTIFF, Shapefile, GeoJSON, dll
    data_type = Column(String(30))

    epsg = Column(Integer)

    width = Column(Integer)

    height = Column(Integer)

    bbox = Column(Geometry("POLYGON", srid=4326))

    file_path = Column(Text)

    status = Column(String(50))