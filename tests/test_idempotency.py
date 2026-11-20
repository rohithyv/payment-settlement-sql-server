"""Idempotency of re-runs, watermark behaviour, and SCD2 on dim_merchant."""
import uuid

from etl import db
from etl.run import run_pipeline
from tests.conftest import GOOD_MERCHANTS, write_csvs


def _fact_count(cn) -> int:
    cur = cn.cursor()
    cur.execute("SELECT COUNT(*) FROM dbo.fact_transaction")
    return cur.fetchone()[0]


def _rows(n: int, day: int = 10) -> list[list]:
    return [
        [str(uuid.uuid4()), f"2026-07-{day + i % 5:02d} 0{i % 9}:00:00",
         "4222222222222222", "Visa", "credit", "M900001", "42.00", "USD", "0"]
        for i in range(n)
    ]


def test_rerun_same_batch_is_idempotent(test_db, tmp_path):
    txn, mer = write_csvs(tmp_path, _rows(20))
    first = run_pipeline("full", txn, mer, database=test_db)
    assert first["loaded"] == 20

    with db.raw_connection(test_db) as cn:
        count_after_first = _fact_count(cn)

    # Re-running the *same* batch incrementally must load zero new rows.
    second = run_pipeline("incremental", txn, mer, database=test_db)
    assert second["loaded"] == 0
    assert second["reconciled"] is True

    with db.raw_connection(test_db) as cn:
        assert _fact_count(cn) == count_after_first == 20


def test_incremental_loads_only_new_rows(test_db, tmp_path):
    base = _rows(10)
    txn, mer = write_csvs(tmp_path, base)
    run_pipeline("full", txn, mer, database=test_db)

    # New batch = old rows + 5 genuinely new, later transactions
    new_rows = _rows(5, day=20)
    txn2, _ = write_csvs(tmp_path, base + new_rows)
    result = run_pipeline("incremental", txn2, mer, database=test_db)
    assert result["loaded"] == 5

    with db.raw_connection(test_db) as cn:
        assert _fact_count(cn) == 15


def test_watermark_advances(test_db, tmp_path):
    txn, mer = write_csvs(tmp_path, _rows(5))
    run_pipeline("full", txn, mer, database=test_db)
    with db.raw_connection(test_db) as cn:
        cur = cn.cursor()
        cur.execute("SELECT last_watermark FROM dbo.etl_watermark WHERE source_name='transactions'")
        wm = cur.fetchone()[0]
    assert wm.year == 2026 and wm.month == 7


def test_scd2_merchant_change_creates_new_version(test_db, tmp_path):
    txn, mer = write_csvs(tmp_path, _rows(3))
    run_pipeline("full", txn, mer, database=test_db)

    # Same merchant id, changed category -> old row expires, new version added
    changed = [("M900001", "Test Grocer", "shopping_pos", "Chicago", "IL"),
               GOOD_MERCHANTS[1]]
    txn2, mer2 = write_csvs(tmp_path, _rows(2, day=22), merchants=changed)
    run_pipeline("incremental", txn2, mer2, database=test_db)

    with db.raw_connection(test_db) as cn:
        cur = cn.cursor()
        cur.execute("""
            SELECT merchant_category, is_current, effective_to
            FROM dbo.dim_merchant WHERE merchant_id = 'M900001'
            ORDER BY effective_from, merchant_key
        """)
        versions = cur.fetchall()
    assert len(versions) == 2
    old, new = versions
    assert old.merchant_category == "grocery_pos" and old.is_current == 0
    assert old.effective_to is not None
    assert new.merchant_category == "shopping_pos" and new.is_current == 1
