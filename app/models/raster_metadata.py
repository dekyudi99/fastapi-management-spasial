from sqlalchemy import Column, Integer, String
from geoalchemy2 import Geometry
from config.database import Base

class RasterMetadata(Base):
    __tablename__ = "raster_metadata"

    id = Column(Integer, primary_key=True)
    layer_name = Column(String, unique=True)
    epsg = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    bbox = Column(Geometry(geometry_type="POLYGON"))
    file_path = Column(String)