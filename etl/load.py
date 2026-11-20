"""Load step: MERGE validated staged rows into the partitioned fact table,
advance the watermark, and reconcile source vs target counts.

Idempotency comes from two layers:
  1. the watermark filter in the stage step (rows below it never re-stage)
  2. the MERGE match on (transaction_id, transaction_date) — re-processing
     the same batch matches every row and inserts nothing.
"""
from datetime import datetime

FACT_MERGE = """
WITH src AS (
    SELECT CONVERT(UNIQUEIDENTIFIER, s.transaction_id)  AS transaction_id,
           CONVERT(DATE, s.transaction_ts)              AS transaction_date,
           CONVERT(INT, CONVERT(CHAR(8), CONVERT(DATE, s.transaction_ts), 112)) AS date_key,
           CONVERT(DATETIME2(0), s.transaction_ts)      AS transaction_ts,
           c.card_key,
           m.merchant_key,
           m.region_key,
           f.fee_key,
           CONVERT(DECIMAL(12,2), s.amount)             AS amount,
           CONVERT(DECIMAL(12,4),
               CONVERT(DECIMAL(12,2), s.amount) * f.interchange_pct + f.fixed_fee) AS fee_amount,
           COALESCE(UPPER(s.currency), 'USD')           AS currency,
           CASE WHEN s.is_fraud = '1' THEN 1 ELSE 0 END AS is_fraud
    FROM dbo.stage_transaction s
    JOIN dbo.dim_card c ON c.card_number = s.card_number
    JOIN dbo.dim_merchant m
        ON m.merchant_id = s.merchant_id
       AND m.effective_from <= CONVERT(DATETIME2(0), s.transaction_ts)
       AND (m.effective_to IS NULL OR m.effective_to > CONVERT(DATETIME2(0), s.transaction_ts))
    JOIN dbo.dim_fee_schedule f
        ON f.card_brand = s.card_brand AND f.merchant_category = m.merchant_category
    WHERE s.is_valid = 1
)
MERGE dbo.fact_transaction AS tgt
USING src
    ON tgt.transaction_id = src.transaction_id
   AND tgt.transaction_date = src.transaction_date
WHEN NOT MATCHED THEN
    INSERT (transaction_id, transaction_date, date_key, transaction_ts, card_key,
            merchant_key, region_key, fee_key, amount, fee_amount, currency,
            is_fraud, etl_run_id)
    VALUES (src.transaction_id, src.transaction_date, src.date_key, src.transaction_ts,
            src.card_key, src.merchant_key, src.region_key, src.fee_key, src.amount,
            src.fee_amount, src.currency, src.is_fraud, ?);
"""


def load_fact(cn, run_id: int) -> int:
    cur = cn.cursor()
    cur.execute(FACT_MERGE, run_id)
    # MERGE rowcount is unreliable across drivers; count what this run inserted
    cur.execute("SELECT COUNT(*) FROM dbo.fact_transaction WHERE etl_run_id = ?", run_id)
    loaded = cur.fetchone()[0]
    cn.commit()
    return loaded


def get_watermark(cn, source: str = "transactions") -> datetime | None:
    cur = cn.cursor()
    cur.execute("SELECT last_watermark FROM dbo.etl_watermark WHERE source_name = ?", source)
    row = cur.fetchone()
    return row[0] if row else None


def advance_watermark(cn, source: str = "transactions") -> None:
    cur = cn.cursor()
    cur.execute("""
        UPDATE dbo.etl_watermark
        SET last_watermark = x.max_ts, updated_at = SYSUTCDATETIME()
        FROM (SELECT MAX(CONVERT(DATETIME2(0), transaction_ts)) AS max_ts
              FROM dbo.stage_transaction WHERE is_valid = 1) x
        WHERE source_name = ? AND x.max_ts IS NOT NULL
    """, source)
    cn.commit()


def reconcile(cn, rows_read: int, skipped_watermark: int, loaded: int, rejected: int) -> dict:
    """Source-vs-target reconciliation.

    Every source row must be accounted for:
        rows_read == skipped(watermark) + already_in_target + loaded + rejected
    """
    cur = cn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM dbo.stage_transaction s
        WHERE s.is_valid = 1
          AND EXISTS (SELECT 1 FROM dbo.fact_transaction f
                      WHERE f.transaction_id = TRY_CONVERT(UNIQUEIDENTIFIER, s.transaction_id)
                        AND f.transaction_date = TRY_CONVERT(DATE, s.transaction_ts))
    """)
    valid_in_target = cur.fetchone()[0]
    already_in_target = valid_in_target - loaded  # matched by MERGE, not inserted now

    accounted = skipped_watermark + already_in_target + loaded + rejected
    ok = accounted == rows_read
    return {
        "rows_read": rows_read,
        "skipped_watermark": skipped_watermark,
        "already_in_target": already_in_target,
        "loaded": loaded,
        "rejected": rejected,
        "reconciled": ok,
    }
