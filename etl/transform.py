"""Transform step: load/refresh dimensions from staged data.

- dim_merchant: SCD Type 2 via MERGE (expire changed rows, insert new versions)
- dim_card:     Type 1 MERGE (insert-if-missing)
dim_date / dim_region / dim_fee_schedule are seeded by the migrations.
"""

SCD2_MERCHANT_MERGE = """
SET NOCOUNT ON;
DECLARE @now DATETIME2(0) = SYSUTCDATETIME();
DECLARE @scd TABLE (
    merge_action      VARCHAR(10),
    merchant_id       VARCHAR(20),
    merchant_name     NVARCHAR(100),
    merchant_category VARCHAR(30),
    city              NVARCHAR(50),
    state_code        CHAR(2),
    region_key        INT
);

-- Step 1 of the SCD2 pattern: MERGE expires changed current rows and inserts
-- brand-new merchants, capturing its OUTPUT into @scd. (SQL Server forbids
-- the nested INSERT..FROM(MERGE..OUTPUT) form on FK-involved tables.)
WITH src AS (
    SELECT s.merchant_id,
           s.merchant_name,
           s.merchant_category,
           s.city,
           UPPER(LEFT(s.state, 2)) AS state_code,
           r.region_key
    FROM dbo.stage_merchant s
    LEFT JOIN dbo.ref_state_region sr ON sr.state_code = UPPER(LEFT(s.state, 2))
    JOIN dbo.dim_region r ON r.region_name = COALESCE(sr.region_name, 'Unknown')
    WHERE s.merchant_id IS NOT NULL
)
MERGE dbo.dim_merchant AS tgt
USING src ON tgt.merchant_id = src.merchant_id AND tgt.is_current = 1
WHEN NOT MATCHED BY TARGET THEN
    INSERT (merchant_id, merchant_name, merchant_category, city, state_code,
            region_key, effective_from, effective_to, is_current)
    VALUES (src.merchant_id, src.merchant_name, src.merchant_category, src.city,
            src.state_code, src.region_key, '1900-01-01', NULL, 1)
WHEN MATCHED AND (
        tgt.merchant_name     <> src.merchant_name
     OR tgt.merchant_category <> src.merchant_category
     OR tgt.city              <> src.city
     OR tgt.state_code        <> src.state_code) THEN
    UPDATE SET tgt.is_current = 0, tgt.effective_to = @now
OUTPUT $action, src.merchant_id, src.merchant_name, src.merchant_category,
       src.city, src.state_code, src.region_key
INTO @scd;

-- Step 2: insert the fresh current version for every merchant just expired.
INSERT INTO dbo.dim_merchant
    (merchant_id, merchant_name, merchant_category, city, state_code, region_key,
     effective_from, effective_to, is_current)
SELECT merchant_id, merchant_name, merchant_category, city, state_code,
       region_key, @now, NULL, 1
FROM @scd WHERE merge_action = 'UPDATE';

SELECT COUNT(*) FROM @scd;
SET NOCOUNT OFF;
"""

CARD_MERGE = """
WITH src AS (
    SELECT card_number,
           MIN(card_brand) AS card_brand,
           MIN(card_type)  AS card_type
    FROM dbo.stage_transaction
    WHERE is_valid = 1 AND card_number IS NOT NULL
      AND card_brand IS NOT NULL AND card_type IS NOT NULL
    GROUP BY card_number
)
MERGE dbo.dim_card AS tgt
USING src ON tgt.card_number = src.card_number
WHEN NOT MATCHED THEN
    INSERT (card_number, card_masked, card_brand, card_type)
    VALUES (src.card_number,
            CONCAT(LEFT(src.card_number, 4), REPLICATE('*', 8), RIGHT(src.card_number, 4)),
            src.card_brand, src.card_type);
"""


def load_dimensions(cn) -> dict[str, int]:
    cur = cn.cursor()
    cur.execute(SCD2_MERCHANT_MERGE)
    while cur.description is None:  # skip DML result counts to reach the SELECT
        cur.nextset()
    merchant_rows = cur.fetchone()[0]
    cur.execute(CARD_MERGE)
    # CTE-prefixed MERGE reports rowcount -1 through pyodbc; ask the session
    cur.execute("SELECT @@ROWCOUNT")
    card_rows = cur.fetchone()[0]
    cn.commit()
    return {"dim_merchant_changed": merchant_rows, "dim_card_inserted": card_rows}
