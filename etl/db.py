"""SQLAlchemy engine / raw pyodbc connection factory for SQL Server."""
from urllib.parse import quote_plus

import pyodbc
import sqlalchemy as sa

from etl import config


def _odbc_str(database: str | None = None, autocommit_db: bool = False) -> str:
    parts = [
        f"DRIVER={{{config.DB_DRIVER}}}",
        f"SERVER={config.DB_SERVER}",
        f"DATABASE={database or config.DB_NAME}",
        f"Trusted_Connection={config.DB_TRUSTED_CONNECTION}",
        f"Encrypt={config.DB_ENCRYPT}",
        "TrustServerCertificate=yes",
    ]
    return ";".join(parts)


def get_engine(database: str | None = None) -> sa.Engine:
    url = "mssql+pyodbc:///?odbc_connect=" + quote_plus(_odbc_str(database))
    return sa.create_engine(url, fast_executemany=True, pool_pre_ping=True)


def raw_connection(database: str | None = None, autocommit: bool = False):
    """Plain pyodbc connection (needed for CREATE DATABASE / SET STATISTICS)."""
    return pyodbc.connect(_odbc_str(database), autocommit=autocommit)


def ensure_database(database: str | None = None) -> None:
    db = database or config.DB_NAME
    with pyodbc.connect(_odbc_str("master"), autocommit=True) as cn:
        cn.execute(
            f"IF DB_ID(N'{db}') IS NULL BEGIN "
            f"CREATE DATABASE [{db}]; "
            f"ALTER DATABASE [{db}] SET RECOVERY SIMPLE; END"
        )
