from sqlalchemy import Column, BigInteger, VARCHAR, Boolean, TIMESTAMP, func
from config.database import Base

class Users(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)
    username = Column(VARCHAR(50), unique=True, nullable=False)
    email = Column(VARCHAR(255), unique=True, nullable=False)
    password = Column(VARCHAR(255), nullable=False)
    role = Column(VARCHAR(20), default="user")
    is_verified = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())