from sqlalchemy import Column, Integer, String, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from config.database import Base
from services.timesatampz_service import TimestampMixin

class LayerGroup(TimestampMixin, Base):
    __tablename__ = "layer_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Workspace tempat layer group berada di GeoServer
    workspace_id = Column(
        Integer,
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False
    )

    # Nama teknis di GeoServer (digunakan untuk parameter WMS LAYERS=workspace:name)
    name = Column(String(150), nullable=False)

    # Judul display untuk WMS GetCapabilities & Dashboard
    title = Column(String(200), nullable=False)

    # Deskripsi layer group
    abstract_text = Column(Text, nullable=True)

    # Mode GeoServer: 'single', 'named', 'container', 'eo'
    mode = Column(String(30), default="single", nullable=False)

    # Identitas user eksternal klien (multiuser S2S: FlowGIS, dsb)
    client_user_id = Column(String(100), nullable=True, index=True)

    # Daftar kata kunci (array string: ["flood", "slope", "dem"])
    keywords = Column(JSON, nullable=True)

    # Relasi SQLAlchemy
    workspace = relationship("Workspace", backref="layer_groups")
    group_layers = relationship(
        "LayerGroupLayer",
        back_populates="layer_group",
        cascade="all, delete-orphan",
        order_by="LayerGroupLayer.layer_order"
    )


class LayerGroupLayer(Base):
    """
    Tabel perantara (junction table) untuk menyimpan daftar layer anggota group,
    urutan rendering (stacking order z-index), dan style override opsional.
    """
    __tablename__ = "layer_group_layers"

    id = Column(Integer, primary_key=True, autoincrement=True)

    layer_group_id = Column(
        Integer,
        ForeignKey("layer_groups.id", ondelete="CASCADE"),
        nullable=False
    )

    layer_id = Column(
        Integer,
        ForeignKey("layers.id", ondelete="CASCADE"),
        nullable=False
    )

    # Urutan rendering (0 = layer terbawah, 1 = di atasnya, dst)
    layer_order = Column(Integer, default=0, nullable=False)

    # SLD Style opsional untuk layer ini di dalam group (jika None, memakai style default layer)
    style_name = Column(String(150), nullable=True)

    # Relasi SQLAlchemy
    layer_group = relationship("LayerGroup", back_populates="group_layers")
    layer = relationship("Layer")
