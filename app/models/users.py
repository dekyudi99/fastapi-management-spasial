from sqlalchemy import Column, BigInteger, Integer, VARCHAR, Boolean, TIMESTAMP, func
from config.database import Base

class Users(Base):
    __tablename__ = "users"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    keycloak_sub = Column(VARCHAR(255), unique=True, nullable=True)
    username = Column(VARCHAR(50), unique=True, nullable=False)
    email = Column(VARCHAR(255), unique=True, nullable=False)
    password = Column(VARCHAR(255), nullable=True)
    # Deprecated: Role kini sepenuhnya dikelola dan diotorisasi melalui Keycloak SSO
    role = Column(VARCHAR(20), default="user")
    # Deprecated: Status verifikasi akun kini ditangani oleh Keycloak
    is_verified = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())