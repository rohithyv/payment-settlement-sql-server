-- =============================================================
-- 020: Partitioned fact table
-- Monthly RIGHT-range partitioning on transaction_date.
-- All indexes are partition-aligned (they include transaction_date).
-- =============================================================

IF NOT EXISTS (SELECT 1 FROM sys.partition_functions WHERE name = 'pf_txn_month')
BEGIN
    DECLARE @sql NVARCHAR(MAX) = N'CREATE PARTITION FUNCTION pf_txn_month (DATE) AS RANGE RIGHT FOR VALUES (';
    DECLARE @d DATE = '2024-01-01';
    WHILE @d < '2027-02-01'
    BEGIN
        SET @sql += QUOTENAME(CONVERT(VARCHAR(10), @d, 120), '''') + N',';
        SET @d = DATEADD(MONTH, 1, @d);
    END;
    SET @sql = LEFT(@sql, LEN(@sql) - 1) + N')';
    EXEC sys.sp_executesql @sql;
END;

IF NOT EXISTS (SELECT 1 FROM sys.partition_schemes WHERE name = 'ps_txn_month')
    CREATE PARTITION SCHEME ps_txn_month AS PARTITION pf_txn_month ALL TO ([PRIMARY]);

IF OBJECT_ID('dbo.fact_transaction') IS NULL
BEGIN
    CREATE TABLE dbo.fact_transaction (
        transaction_sk   BIGINT IDENTITY(1,1) NOT NULL,
        transaction_id   UNIQUEIDENTIFIER NOT NULL,        -- degenerate/business key
        transaction_date DATE             NOT NULL,        -- partitioning column
        date_key         INT              NOT NULL,
        transaction_ts   DATETIME2(0)     NOT NULL,
        card_key         INT              NOT NULL,
        merchant_key     INT              NOT NULL,
        region_key       INT              NOT NULL,
        fee_key          INT              NOT NULL,
        amount           DECIMAL(12,2)    NOT NULL,
        fee_amount       DECIMAL(12,4)    NOT NULL,
        currency         CHAR(3)          NOT NULL,
        is_fraud         BIT              NOT NULL,
        etl_run_id       INT              NOT NULL,
        CONSTRAINT PK_fact_transaction PRIMARY KEY CLUSTERED (transaction_date, transaction_sk)
            ON ps_txn_month (transaction_date),
        CONSTRAINT FK_fact_date     FOREIGN KEY (date_key)     REFERENCES dbo.dim_date (date_key),
        CONSTRAINT FK_fact_card     FOREIGN KEY (card_key)     REFERENCES dbo.dim_card (card_key),
        CONSTRAINT FK_fact_merchant FOREIGN KEY (merchant_key) REFERENCES dbo.dim_merchant (merchant_key),
        CONSTRAINT FK_fact_region   FOREIGN KEY (region_key)   REFERENCES dbo.dim_region (region_key),
        CONSTRAINT FK_fact_fee      FOREIGN KEY (fee_key)      REFERENCES dbo.dim_fee_schedule (fee_key)
    ) ON ps_txn_month (transaction_date);

    -- idempotency guard: one row per business key (aligned unique index)
    CREATE UNIQUE NONCLUSTERED INDEX UX_fact_transaction_id
        ON dbo.fact_transaction (transaction_id, transaction_date)
        ON ps_txn_month (transaction_date);

    -- FK support indexes
    CREATE NONCLUSTERED INDEX IX_fact_card ON dbo.fact_transaction (card_key)
        ON ps_txn_month (transaction_date);
    CREATE NONCLUSTERED INDEX IX_fact_merchant ON dbo.fact_transaction (merchant_key)
        ON ps_txn_month (transaction_date);
    -- NOTE: the covering index for the monthly-fees query is intentionally NOT
    -- created here; perf/fix_index.sql adds it as part of the tuning exercise.
END;
GO
