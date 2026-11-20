"""Stage step: BULK INSERT the CSVs into raw staging, then copy into the
typed-as-text staging table, applying the incremental watermark filter.
"""
from datetime import datetime


def stage(cn, extract_info: dict, watermark: datetime | None) -> dict:
    """Returns counts: staged (rows to process) and skipped (<= watermark)."""
    cur = cn.cursor()
    cur.execute("TRUNCATE TABLE dbo.stage_transaction_raw")
    cur.execute("TRUNCATE TABLE dbo.stage_transaction")
    cur.execute("TRUNCATE TABLE dbo.stage_merchant")

    cur.execute(f"""
        BULK INSERT dbo.stage_transaction_raw
        FROM '{extract_info["csv_path"]}'
        WITH (FIRSTROW = 2, FIELDTERMINATOR = ',', ROWTERMINATOR = '0x0a',
              CODEPAGE = '65001', TABLOCK)
    """)
    cur.execute(f"""
        BULK INSERT dbo.stage_merchant
        FROM '{extract_info["merchants_path"]}'
        WITH (FIRSTROW = 2, FIELDTERMINATOR = ',', ROWTERMINATOR = '0x0a',
              CODEPAGE = '65001', TABLOCK)
    """)

    # Watermark filter: only rows newer than (or at) the last loaded timestamp
    # move forward. Rows at exactly the watermark are re-processed and the
    # MERGE into the fact table makes them no-ops, so re-runs are idempotent.
    wm_filter = ""
    params: list = []
    if watermark is not None:
        wm_filter = "WHERE TRY_CONVERT(DATETIME2(0), transaction_ts) >= ? OR TRY_CONVERT(DATETIME2(0), transaction_ts) IS NULL"
        params.append(watermark)

    cur.execute(f"""
        INSERT INTO dbo.stage_transaction
            (transaction_id, transaction_ts, card_number, card_brand, card_type,
             merchant_id, amount, currency, is_fraud)
        SELECT NULLIF(LTRIM(RTRIM(transaction_id)), ''),
               NULLIF(LTRIM(RTRIM(transaction_ts)), ''),
               NULLIF(LTRIM(RTRIM(card_number)), ''),
               NULLIF(LTRIM(RTRIM(card_brand)), ''),
               NULLIF(LTRIM(RTRIM(card_type)), ''),
               NULLIF(LTRIM(RTRIM(merchant_id)), ''),
               NULLIF(LTRIM(RTRIM(amount)), ''),
               NULLIF(LTRIM(RTRIM(currency)), ''),
               NULLIF(LTRIM(RTRIM(is_fraud)), '')
        FROM dbo.stage_transaction_raw {wm_filter}
    """, *params)
    staged = cur.rowcount

    cur.execute("SELECT COUNT(*) FROM dbo.stage_transaction_raw")
    raw_total = cur.fetchone()[0]
    cn.commit()
    return {"staged": staged, "skipped_watermark": raw_total - staged}
