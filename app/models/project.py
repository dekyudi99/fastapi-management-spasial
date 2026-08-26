from sqlalchemy import Column, Integer, ForeignKey, String, DateTime
from config.database import Base
from sqlalchemy.sql import func
from services.timesatampz_service import TimestampMixin

class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False
    )

    project_name = Column(String(100), nullable=False)

    description = Column(String(255))