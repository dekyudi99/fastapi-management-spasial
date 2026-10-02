from sqlalchemy import Column, Integer, BigInteger, ForeignKey, String, Boolean, DateTime
from config.database import Base
from sqlalchemy.sql import func
from services.timesatampz_service import TimestampMixin

class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)

    user_id = Column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True
    )

    name = Column(String(100), nullable=False, default="Standard API Key")
    microservice_key_id = Column(String(100), nullable=True)
    key_prefix = Column(String(20), nullable=False, default="gsvc_sk_")
    masked_key = Column(String(50), nullable=True)
    api_key_secret = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)