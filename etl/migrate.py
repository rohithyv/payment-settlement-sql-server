"""Run the T-SQL migration files in sql/ in filename order.

Usage: python -m etl.migrate [--database NAME]
"""
import argparse
import re

from etl import config, db

GO_SPLIT = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)


def run_migrations(database: str | None = None) -> None:
    db.ensure_database(database)
    with db.raw_connection(database, autocommit=True) as cn:
        for path in sorted(config.SQL_DIR.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            for batch in GO_SPLIT.split(sql):
                if batch.strip():
                    cn.execute(batch)
            print(f"applied {path.name}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--database", default=None)
    run_migrations(p.parse_args().database)
