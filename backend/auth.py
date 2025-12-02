# backend/auth.py
import os
from datetime import datetime, timedelta
from jose import jwt
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get secret key
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY or SECRET_KEY.strip() == "":
    raise ValueError("❌ ERROR: SECRET_KEY is missing in .env file!")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours


def create_access_token(user_id: int, extra_data: dict = None):
    """
    Create a secure JWT token.
    Required:
        - user_id (int)
    Optional:
        - extra_data (dict)
    """
    if extra_data is None:
        extra_data = {}

    to_encode = {
        "sub": str(user_id),               # REQUIRED for identification
        "iat": datetime.utcnow(),          # issued at
        "nbf": datetime.utcnow(),          # not valid before
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iss": "KEN-Assistant",            # issuer
        **extra_data                       # additional user metadata
    }

    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
