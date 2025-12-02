# database.py

import os
from mysql.connector.pooling import MySQLConnectionPool
from mysql.connector import Error
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

POOL = None

def init_pool():
    global POOL
    if POOL is None:
        try:
            POOL = MySQLConnectionPool(
                pool_name="ken_pool",
                pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", "3306")),
                database=os.getenv("DB_NAME", "local_ai_assistant"),
                user=os.getenv("DB_USER", "root"),
                password=os.getenv("DB_PASSWORD", "")
            )
            print("[DB] Connection Pool Initialized")
        except Error as e:
            print(f"[DB] Pool initialization error: {e}")
            POOL = None
            raise e


def get_db_connection():
    global POOL
    try:
        if POOL is None:
            init_pool()

        conn = POOL.get_connection()

        if conn is None:
            print("[DB] Failed to get a pooled connection.")
            return None

        return conn

    except Error as e:
        print(f"[DB] Connection error: {e}")
        POOL = None   # reset pool so it initializes again next time
        return None
