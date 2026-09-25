from sqlalchemy import Column, BigInteger, String, DateTime, func
from config.database import Base
from services.timesatampz_service import TimestampMixin

class EmailOTP(TimestampMixin, Base):
    __tablename__ = "email_otps"

    id = Column(BigInteger, primary_key=True, index=True)
    email = Column(String(255), index=True, nullable=False)
    otp = Column(String(6), nullable=False)
    type = Column(String(20), nullable=False, default="register")  # 'register' atau 'reset_password'
    expires_at = Column(DateTime, nullable=False)
