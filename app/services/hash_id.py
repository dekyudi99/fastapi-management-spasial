import os
from dotenv import load_dotenv
from sqids import Sqids

load_dotenv()

sqids = Sqids(min_length=16, alphabet=os.getenv("SECRET_ALPHABET"))

def encode_id(num: int) -> str:
    return sqids.encode([num])

def decode_id(code: str) -> int | None:
    numbers = sqids.decode(code)
    return numbers[0] if numbers else None