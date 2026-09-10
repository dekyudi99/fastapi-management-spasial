from sqlalchemy import Column, Integer, BigInteger, String, ForeignKey, Text, JSON
from config.database import Base
from services.timesatampz_service import TimestampMixin

class Logs(TimestampMixin, Base):
    __tablename__ = "logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Sumber autentikasi: 'JWT' (Web Dashboard) atau 'API_KEY' (S2S / FlowGIS)
    auth_type = Column(String(20), nullable=False)

    # Aktor AstraGIS (jika login web dashboard)
    user_id = Column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    # Aktor API Key & Project (jika aksi dilakukan via API Key S2S)
    api_key_id = Column(
        Integer,
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True
    )
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True
    )

    # Identitas multi-user dari sistem klien (misal FlowGIS / end-user)
    client_user_id = Column(String(100), nullable=True)
    client_user_email = Column(String(150), nullable=True)
    client_user_name = Column(String(150), nullable=True)

    # Tindakan & Target
    action = Column(String(50), nullable=False)             # contoh: 'LAYER_PUBLISH_S2S', 'LAYER_DELETE'
    resource_type = Column(String(30), nullable=False)      # contoh: 'LAYER', 'WORKSPACE', 'PROJECT'
    resource_id = Column(String(100), nullable=True)        # ID resource target
    resource_name = Column(String(150), nullable=True)      # Nama layer / workspace / project

    # Status eksekusi & detail teknis
    status = Column(String(20), default="SUCCESS", nullable=False)  # 'SUCCESS' / 'FAILED'
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)

    # Metadata fleksibel (JSON) untuk request body, parameter opsional, atau trace error
    meta_data = Column(JSON, nullable=True)
