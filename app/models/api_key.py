from sqlalchemy import Column, Integer, ForeignKey, String, Boolean, DateTime
from config.database import Base
from sqlalchemy.sql import func
from services.timesatampz_service import TimestampMixin

class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id"),
        nullable=False
    )

    name = Column(String(100), nullable=False)

    api_key_hash = Column(String(255), nullable=False)

    is_active = Column(Boolean, default=True)