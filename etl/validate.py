"""Validation rules, all set-based T-SQL over the staging table.

Each rule marks failing rows (first failing rule wins). Two groups:
  - pre-transform rules (nulls, format, ranges, duplicates)
  - referential-integrity rules, run AFTER dimensions are loaded
Finally, rejected rows are copied to dbo.rejected_rows as JSON with the
rule name and a timestamp.
"""
from etl import config

# rule name -> predicate over still-valid rows
PRE_RULES: list[tuple[str, str]] = [
    ("null_required_column",
     """transaction_id IS NULL OR transaction_ts IS NULL OR card_number IS NULL
        OR merchant_id IS NULL OR amount IS NULL"""),
    ("invalid_transaction_id",
     "TRY_CONVERT(UNIQUEIDENTIFIER, transaction_id) IS NULL"),
    ("invalid_or_out_of_range_date",
     f"""TRY_CONVERT(DATETIME2(0), transaction_ts) IS NULL
        OR TRY_CONVERT(DATETIME2(0), transaction_ts) <  '{config.DATE_MIN}'
        OR TRY_CONVERT(DATETIME2(0), transaction_ts) >= '{config.DATE_MAX}'"""),
    ("invalid_or_out_of_range_amount",
     f"""TRY_CONVERT(DECIMAL(12,2), amount) IS NULL
        OR TRY_CONVERT(DECIMAL(12,2), amount) < {config.AMOUNT_MIN}
        OR TRY_CONVERT(DECIMAL(12,2), amount) > {config.AMOUNT_MAX}"""),
]

DUP_RULE = ("duplicate_transaction_id", """
    ;WITH d AS (
        SELECT stage_id,
               ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY stage_id) AS rn
        FROM dbo.stage_transaction WHERE is_valid = 1
    )
    UPDATE s SET is_valid = 0, reject_rule = 'duplicate_transaction_id'
    FROM dbo.stage_transaction s JOIN d ON d.stage_id = s.stage_id
    WHERE d.rn > 1
""")

# RI rules assume dimensions are already loaded for this batch.
RI_RULES: list[tuple[str, str]] = [
    ("ri_merchant_not_found",
     """NOT EXISTS (SELECT 1 FROM dbo.dim_merchant m
                    WHERE m.merchant_id = dbo.stage_transaction.merchant_id
                      AND m.effective_from <= TRY_CONVERT(DATETIME2(0), transaction_ts)
                      AND (m.effective_to IS NULL
                           OR m.effective_to > TRY_CONVERT(DATETIME2(0), transaction_ts)))"""),
    ("ri_card_not_found",
     """NOT EXISTS (SELECT 1 FROM dbo.dim_card c
                    WHERE c.card_number = dbo.stage_transaction.card_number)"""),
    ("ri_date_not_found",
     """NOT EXISTS (SELECT 1 FROM dbo.dim_date d
                    WHERE d.full_date = TRY_CONVERT(DATE, transaction_ts))"""),
    ("ri_region_not_found",
     """NOT EXISTS (SELECT 1 FROM dbo.dim_merchant m
                    JOIN dbo.dim_region r ON r.region_key = m.region_key
                    WHERE m.merchant_id = dbo.stage_transaction.merchant_id)"""),
    ("ri_fee_schedule_not_found",
     """NOT EXISTS (SELECT 1 FROM dbo.dim_fee_schedule f
                    JOIN dbo.dim_merchant m ON m.merchant_category = f.merchant_category
                    WHERE f.card_brand = dbo.stage_transaction.card_brand
                      AND m.merchant_id = dbo.stage_transaction.merchant_id
                      AND m.is_current = 1)"""),
]


def _apply_predicate_rule(cur, rule: str, predicate: str) -> int:
    cur.execute(f"""
        UPDATE dbo.stage_transaction
        SET is_valid = 0, reject_rule = '{rule}'
        WHERE is_valid = 1 AND ({predicate})
    """)
    return cur.rowcount


def validate_pre(cn) -> dict[str, int]:
    """Null/format/range/duplicate rules — before dimension loading."""
    cur = cn.cursor()
    counts: dict[str, int] = {}
    for rule, predicate in PRE_RULES:
        counts[rule] = _apply_predicate_rule(cur, rule, predicate)
    cur.execute(DUP_RULE[1])
    counts[DUP_RULE[0]] = cur.rowcount
    cn.commit()
    return counts


def validate_ri(cn) -> dict[str, int]:
    """Referential integrity against every dimension — after dim loading."""
    cur = cn.cursor()
    counts: dict[str, int] = {}
    for rule, predicate in RI_RULES:
        counts[rule] = _apply_predicate_rule(cur, rule, predicate)
    cn.commit()
    return counts


def persist_rejects(cn, run_id: int) -> int:
    """Copy every invalid staged row into rejected_rows as JSON."""
    cur = cn.cursor()
    cur.execute("""
        INSERT INTO dbo.rejected_rows (etl_run_id, rule_failed, row_payload)
        SELECT ?, s.reject_rule,
               (SELECT s2.transaction_id, s2.transaction_ts, s2.card_number,
                       s2.card_brand, s2.card_type, s2.merchant_id, s2.amount,
                       s2.currency, s2.is_fraud
                FROM dbo.stage_transaction s2 WHERE s2.stage_id = s.stage_id
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES)
        FROM dbo.stage_transaction s
        WHERE s.is_valid = 0
    """, run_id)
    rejected = cur.rowcount
    cn.commit()
    return rejected
