"""Configuration loaded from .env at the project root."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DB_SERVER = os.getenv("DB_SERVER", r"(localdb)\MSSQLLocalDB")
DB_NAME = os.getenv("DB_NAME", "settlement_wh")
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
DB_TRUSTED_CONNECTION = os.getenv("DB_TRUSTED_CONNECTION", "yes")
DB_ENCRYPT = os.getenv("DB_ENCRYPT", "no")
DB_TEST_NAME = os.getenv("DB_TEST_NAME", "settlement_wh_test")

DATA_DIR = PROJECT_ROOT / "data"
SQL_DIR = PROJECT_ROOT / "sql"

# Validation bounds
AMOUNT_MIN = 0.01
AMOUNT_MAX = 100_000.00
DATE_MIN = "2024-01-01"
DATE_MAX = "2027-01-01"
