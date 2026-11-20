-- =============================================================
-- 030: ETL infrastructure — staging, rejects, audit log, watermark
-- =============================================================

-- Landing heap for BULK INSERT: column-for-column mirror of the CSV.
IF OBJECT_ID('dbo.stage_transaction_raw') IS NULL
BEGIN
    CREATE TABLE dbo.stage_transaction_raw (
        transaction_id NVARCHAR(50)  NULL,
        transaction_ts NVARCHAR(30)  NULL,
        card_number    NVARCHAR(30)  NULL,
        card_brand     NVARCHAR(20)  NULL,
        card_type      NVARCHAR(10)  NULL,
        merchant_id    NVARCHAR(30)  NULL,
        amount         NVARCHAR(30)  NULL,
        currency       NVARCHAR(10)  NULL,
        is_fraud       NVARCHAR(5)   NULL
    );
END;

-- Raw staging: everything lands as text exactly as extracted; typing
-- happens in the transform step after validation.
IF OBJECT_ID('dbo.stage_transaction') IS NULL
BEGIN
    CREATE TABLE dbo.stage_transaction (
        stage_id       BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_stage_transaction PRIMARY KEY CLUSTERED,
        transaction_id NVARCHAR(50)  NULL,
        transaction_ts NVARCHAR(30)  NULL,
        card_number    NVARCHAR(30)  NULL,
        card_brand     NVARCHAR(20)  NULL,
        card_type      NVARCHAR(10)  NULL,
        merchant_id    NVARCHAR(30)  NULL,
        amount         NVARCHAR(30)  NULL,
        currency       NVARCHAR(10)  NULL,
        is_fraud       NVARCHAR(5)   NULL,
        is_valid       BIT           NOT NULL CONSTRAINT DF_stage_valid DEFAULT 1,
        reject_rule    VARCHAR(60)   NULL
    );
END;

IF OBJECT_ID('dbo.stage_merchant') IS NULL
BEGIN
    CREATE TABLE dbo.stage_merchant (
        merchant_id       NVARCHAR(30)  NULL,
        merchant_name     NVARCHAR(200) NULL,
        merchant_category NVARCHAR(50)  NULL,
        city              NVARCHAR(100) NULL,
        state             NVARCHAR(10)  NULL
    );
END;

IF OBJECT_ID('dbo.rejected_rows') IS NULL
BEGIN
    CREATE TABLE dbo.rejected_rows (
        reject_id   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_rejected_rows PRIMARY KEY CLUSTERED,
        etl_run_id  INT           NOT NULL,
        rule_failed VARCHAR(60)   NOT NULL,
        row_payload NVARCHAR(MAX) NOT NULL,   -- the offending row as JSON
        rejected_at DATETIME2(0)  NOT NULL CONSTRAINT DF_rejected_at DEFAULT SYSUTCDATETIME()
    );
    CREATE NONCLUSTERED INDEX IX_rejected_run ON dbo.rejected_rows (etl_run_id, rule_failed);
END;

IF OBJECT_ID('dbo.etl_run_log') IS NULL
BEGIN
    CREATE TABLE dbo.etl_run_log (
        etl_run_id    INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_etl_run_log PRIMARY KEY CLUSTERED,
        run_mode      VARCHAR(12)   NOT NULL,     -- full | incremental
        source_file   NVARCHAR(260) NULL,
        started_at    DATETIME2(0)  NOT NULL CONSTRAINT DF_run_started DEFAULT SYSUTCDATETIME(),
        ended_at      DATETIME2(0)  NULL,
        rows_read     INT           NULL,
        rows_loaded   INT           NULL,
        rows_rejected INT           NULL,
        status        VARCHAR(12)   NOT NULL CONSTRAINT DF_run_status DEFAULT 'running',
        message       NVARCHAR(2000) NULL
    );
END;

IF OBJECT_ID('dbo.etl_watermark') IS NULL
BEGIN
    CREATE TABLE dbo.etl_watermark (
        source_name    VARCHAR(50)  NOT NULL CONSTRAINT PK_etl_watermark PRIMARY KEY CLUSTERED,
        last_watermark DATETIME2(0) NOT NULL,
        updated_at     DATETIME2(0) NOT NULL CONSTRAINT DF_wm_updated DEFAULT SYSUTCDATETIME()
    );
    INSERT INTO dbo.etl_watermark (source_name, last_watermark)
    VALUES ('transactions', '1900-01-01');
END;
GO
