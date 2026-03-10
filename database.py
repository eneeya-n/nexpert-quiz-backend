import os
import pymysql
from pymysql.cursors import DictCursor
from dotenv import load_dotenv

load_dotenv()

ca_path = os.getenv("DB_CA_PATH") or os.getenv("CA_PATH")
ssl_config = {"ca": ca_path} if ca_path and os.path.isfile(ca_path) else {"ca": None}

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 4000)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "ssl": ssl_config,
    "cursorclass": DictCursor,
    "connect_timeout": 10,
    "read_timeout": 10,
    "write_timeout": 10,
    "autocommit": False,
}


def get_connection():
    return pymysql.connect(**DB_CONFIG)


def init_db():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(150) NOT NULL UNIQUE,
                    score INT DEFAULT 0,
                    violation_count INT DEFAULT 0,
                    tab_switch_count INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Keep existing deployments compatible by adding column if table already exists.
            cur.execute("""
                ALTER TABLE students
                ADD COLUMN IF NOT EXISTS tab_switch_count INT DEFAULT 0
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS violations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    violation_type VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    finally:
        conn.close()
