"""Integration-test fixtures: a dedicated test database plus a small,
hand-crafted batch containing exactly one failure per validation rule.
"""
import csv
import uuid
from pathlib import Path

import pyodbc
import pytest

from etl import config, db
from etl.run import run_pipeline

TEST_DB = config.DB_TEST_NAME

GOOD_MERCHANTS = [
    ("M900001", "Test Grocer", "grocery_pos", "Chicago", "IL"),
    ("M900002", "Test Diner", "restaurant", "Austin", "TX"),
]

def _uuid() -> str:
    return str(uuid.uuid4())

# One deliberate failure per rule; everything else is clean.
DUP_ID = _uuid()
GOOD_ROWS = [
    [_uuid(), "2026-06-01 10:00:00", "4111111111111111", "Visa", "credit", "M900001", "25.00", "USD", "0"],
    [_uuid(), "2026-06-02 11:30:00", "5500000000000004", "Mastercard", "debit", "M900002", "80.50", "USD", "0"],
    [_uuid(), "2026-06-03 09:15:00", "371111111111114", "Amex", "credit", "M900001", "12.99", "USD", "0"],
    [DUP_ID,  "2026-06-04 12:00:00", "4111111111111111", "Visa", "credit", "M900002", "45.00", "USD", "0"],
]
BAD_ROWS = {
    "null_required_column":
        ["", "2026-06-05 10:00:00", "4111111111111111", "Visa", "credit", "M900001", "10.00", "USD", "0"],
    "invalid_transaction_id":
        ["not-a-guid", "2026-06-05 10:05:00", "4111111111111111", "Visa", "credit", "M900001", "10.00", "USD", "0"],
    "invalid_or_out_of_range_date":
        [_uuid(), "1999-01-01 00:00:00", "4111111111111111", "Visa", "credit", "M900001", "10.00", "USD", "0"],
    "invalid_or_out_of_range_amount":
        [_uuid(), "2026-06-05 10:10:00", "4111111111111111", "Visa", "credit", "M900001", "-4.00", "USD", "0"],
    "duplicate_transaction_id":
        [DUP_ID, "2026-06-04 12:00:00", "4111111111111111", "Visa", "credit", "M900002", "45.00", "USD", "0"],
    "ri_merchant_not_found":
        [_uuid(), "2026-06-06 10:00:00", "4111111111111111", "Visa", "credit", "MBAD001", "10.00", "USD", "0"],
}


def write_csvs(tmp: Path, rows: list[list], merchants=GOOD_MERCHANTS) -> tuple[Path, Path]:
    txn = tmp / "transactions.csv"
    mer = tmp / "merchants.csv"
    with open(txn, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["transaction_id", "transaction_ts", "card_number", "card_brand",
                    "card_type", "merchant_id", "amount", "currency", "is_fraud"])
        w.writerows(rows)
    with open(mer, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["merchant_id", "merchant_name", "merchant_category", "city", "state"])
        w.writerows(merchants)
    return txn, mer


@pytest.fixture(scope="session")
def test_db():
    """Fresh test database for the whole session."""
    with pyodbc.connect(db._odbc_str("master"), autocommit=True) as cn:
        cn.execute(f"""
            IF DB_ID(N'{TEST_DB}') IS NOT NULL BEGIN
                ALTER DATABASE [{TEST_DB}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
                DROP DATABASE [{TEST_DB}];
            END
        """)
    yield TEST_DB


@pytest.fixture(scope="session")
def pipeline_result(test_db, tmp_path_factory):
    """One full run over the crafted batch; shared by the validation tests."""
    tmp = tmp_path_factory.mktemp("batch")
    rows = GOOD_ROWS + list(BAD_ROWS.values())
    txn, mer = write_csvs(tmp, rows)
    summary = run_pipeline("full", txn, mer, database=test_db)
    return summary


@pytest.fixture()
def test_conn(test_db):
    cn = db.raw_connection(test_db)
    yield cn
    cn.close()
