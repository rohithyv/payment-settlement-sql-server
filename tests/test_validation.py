"""One test per validation rule, against the shared crafted batch."""
import pytest


def _rejects_by_rule(cn, run_id: int) -> dict[str, int]:
    cur = cn.cursor()
    cur.execute("""
        SELECT rule_failed, COUNT(*) FROM dbo.rejected_rows
        WHERE etl_run_id = ? GROUP BY rule_failed
    """, run_id)
    return dict(cur.fetchall())


@pytest.mark.parametrize("rule", [
    "null_required_column",
    "invalid_transaction_id",
    "invalid_or_out_of_range_date",
    "invalid_or_out_of_range_amount",
    "duplicate_transaction_id",
    "ri_merchant_not_found",
])
def test_rule_rejects_exactly_one_row(pipeline_result, test_conn, rule):
    rejects = _rejects_by_rule(test_conn, pipeline_result["run_id"])
    assert rejects.get(rule) == 1, f"expected 1 reject for {rule}, got {rejects}"


def test_rejected_rows_have_payload_and_timestamp(pipeline_result, test_conn):
    cur = test_conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM dbo.rejected_rows
        WHERE etl_run_id = ?
          AND ISJSON(row_payload) = 1 AND rejected_at IS NOT NULL
    """, pipeline_result["run_id"])
    assert cur.fetchone()[0] == pipeline_result["rejected"] == 6


def test_only_good_rows_loaded(pipeline_result, test_conn):
    cur = test_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM dbo.fact_transaction")
    assert cur.fetchone()[0] == 4  # the 4 GOOD_ROWS (dup kept once)
    assert pipeline_result["loaded"] == 4


def test_reconciliation_and_audit_log(pipeline_result, test_conn):
    assert pipeline_result["reconciled"] is True
    cur = test_conn.cursor()
    cur.execute("""
        SELECT status, rows_read, rows_loaded, rows_rejected, ended_at
        FROM dbo.etl_run_log WHERE etl_run_id = ?
    """, pipeline_result["run_id"])
    status, rows_read, loaded, rejected, ended = cur.fetchone()
    assert status == "success"
    assert (rows_read, loaded, rejected) == (10, 4, 6)
    assert ended is not None


def test_fee_amount_matches_schedule(pipeline_result, test_conn):
    """fee = amount * interchange_pct + fixed_fee for every loaded row."""
    cur = test_conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM dbo.fact_transaction f
        JOIN dbo.dim_fee_schedule s ON s.fee_key = f.fee_key
        WHERE f.fee_amount <> CONVERT(DECIMAL(12,4), f.amount * s.interchange_pct + s.fixed_fee)
    """)
    assert cur.fetchone()[0] == 0
