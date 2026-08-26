from sqlalchemy import Column, Integer, ForeignKey, String, Boolean, DateTime
from config.database import Base
from sqlalchemy.sql import func
from services.timesatampz_service import TimestampMixin

class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id"),
        nullable=False
    )

    name = Column(String(100), nullable=False)

    ws_name = Column(String(100), nullable=False)