-- =============================================================
-- 010: Dimension tables (star schema)
-- Idempotent: safe to run repeatedly.
-- =============================================================

-- ---------- dim_date ----------
IF OBJECT_ID('dbo.dim_date') IS NULL
BEGIN
    CREATE TABLE dbo.dim_date (
        date_key        INT          NOT NULL CONSTRAINT PK_dim_date PRIMARY KEY CLUSTERED, -- yyyymmdd
        full_date       DATE         NOT NULL,
        [year]          SMALLINT     NOT NULL,
        [quarter]       TINYINT      NOT NULL,
        [month]         TINYINT      NOT NULL,
        month_name      VARCHAR(10)  NOT NULL,
        year_month      CHAR(7)      NOT NULL,  -- '2026-08'
        [day]           TINYINT      NOT NULL,
        day_of_week     TINYINT      NOT NULL,
        day_name        VARCHAR(10)  NOT NULL,
        is_weekend      BIT          NOT NULL
    );
    CREATE UNIQUE NONCLUSTERED INDEX UX_dim_date_full_date ON dbo.dim_date (full_date);
END;

IF NOT EXISTS (SELECT 1 FROM dbo.dim_date)
BEGIN
    ;WITH n AS (
        SELECT TOP (1200) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS i
        FROM sys.all_objects a CROSS JOIN sys.all_objects b
    ),
    d AS (SELECT DATEADD(DAY, i, '2024-01-01') AS dt FROM n WHERE DATEADD(DAY, i, '2024-01-01') < '2027-01-01')
    INSERT INTO dbo.dim_date
    SELECT CONVERT(INT, FORMAT(dt, 'yyyyMMdd')), dt,
           YEAR(dt), DATEPART(QUARTER, dt), MONTH(dt), DATENAME(MONTH, dt),
           FORMAT(dt, 'yyyy-MM'), DAY(dt), DATEPART(WEEKDAY, dt), DATENAME(WEEKDAY, dt),
           CASE WHEN DATENAME(WEEKDAY, dt) IN ('Saturday', 'Sunday') THEN 1 ELSE 0 END
    FROM d;
END;

-- ---------- dim_region ----------
IF OBJECT_ID('dbo.dim_region') IS NULL
BEGIN
    CREATE TABLE dbo.dim_region (
        region_key   INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_dim_region PRIMARY KEY CLUSTERED,
        region_name  VARCHAR(20) NOT NULL,
        country      CHAR(2)     NOT NULL CONSTRAINT DF_dim_region_country DEFAULT 'US',
        CONSTRAINT UX_dim_region_name UNIQUE (region_name)
    );
END;

MERGE dbo.dim_region AS tgt
USING (VALUES ('Northeast'), ('Midwest'), ('South'), ('West'), ('Unknown')) AS src(region_name)
    ON tgt.region_name = src.region_name
WHEN NOT MATCHED THEN INSERT (region_name) VALUES (src.region_name);

-- state -> region lookup used by the transform layer
IF OBJECT_ID('dbo.ref_state_region') IS NULL
BEGIN
    CREATE TABLE dbo.ref_state_region (
        state_code CHAR(2) NOT NULL CONSTRAINT PK_ref_state_region PRIMARY KEY,
        region_name VARCHAR(20) NOT NULL
    );
    INSERT INTO dbo.ref_state_region VALUES
        ('NY','Northeast'),('MA','Northeast'),('PA','Northeast'),('NJ','Northeast'),
        ('IL','Midwest'),('OH','Midwest'),('MI','Midwest'),('MN','Midwest'),('WI','Midwest'),
        ('TX','South'),('FL','South'),('GA','South'),('NC','South'),('TN','South'),('VA','South'),
        ('CA','West'),('WA','West'),('OR','West'),('CO','West'),('AZ','West'),('NV','West');
END;

-- ---------- dim_card ----------
IF OBJECT_ID('dbo.dim_card') IS NULL
BEGIN
    CREATE TABLE dbo.dim_card (
        card_key     INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_dim_card PRIMARY KEY CLUSTERED,
        card_number  VARCHAR(20)  NOT NULL,   -- synthetic PAN; real systems store a token/hash
        card_masked  VARCHAR(20)  NOT NULL,
        card_brand   VARCHAR(12)  NOT NULL,
        card_type    VARCHAR(6)   NOT NULL,
        created_at   DATETIME2(0) NOT NULL CONSTRAINT DF_dim_card_created DEFAULT SYSUTCDATETIME()
    );
    CREATE UNIQUE NONCLUSTERED INDEX UX_dim_card_number ON dbo.dim_card (card_number);
END;

-- ---------- dim_merchant (SCD Type 2) ----------
IF OBJECT_ID('dbo.dim_merchant') IS NULL
BEGIN
    CREATE TABLE dbo.dim_merchant (
        merchant_key      INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_dim_merchant PRIMARY KEY CLUSTERED,
        merchant_id       VARCHAR(20)   NOT NULL,      -- natural/business key
        merchant_name     NVARCHAR(100) NOT NULL,
        merchant_category VARCHAR(30)   NOT NULL,
        city              NVARCHAR(50)  NOT NULL,
        state_code        CHAR(2)       NOT NULL,
        region_key        INT           NOT NULL
            CONSTRAINT FK_dim_merchant_region REFERENCES dbo.dim_region (region_key),
        effective_from    DATETIME2(0)  NOT NULL,
        effective_to      DATETIME2(0)  NULL,          -- NULL = open-ended current row
        is_current        BIT           NOT NULL CONSTRAINT DF_dim_merchant_current DEFAULT 1
    );
    CREATE NONCLUSTERED INDEX IX_dim_merchant_natural ON dbo.dim_merchant (merchant_id, is_current)
        INCLUDE (merchant_name, merchant_category, city, state_code);
END;

-- ---------- dim_fee_schedule ----------
IF OBJECT_ID('dbo.dim_fee_schedule') IS NULL
BEGIN
    CREATE TABLE dbo.dim_fee_schedule (
        fee_key           INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_dim_fee_schedule PRIMARY KEY CLUSTERED,
        card_brand        VARCHAR(12)  NOT NULL,
        merchant_category VARCHAR(30)  NOT NULL,
        interchange_pct   DECIMAL(6,4) NOT NULL,   -- e.g. 0.0151 = 1.51%
        fixed_fee         DECIMAL(6,2) NOT NULL,
        CONSTRAINT UX_dim_fee_schedule UNIQUE (card_brand, merchant_category)
    );
END;

IF NOT EXISTS (SELECT 1 FROM dbo.dim_fee_schedule)
BEGIN
    ;WITH brands AS (
        SELECT * FROM (VALUES ('Visa', 0.0151, 0.10), ('Mastercard', 0.0158, 0.10),
                              ('Amex', 0.0230, 0.15), ('Discover', 0.0156, 0.10)
        ) AS b(card_brand, base_pct, fixed_fee)
    ),
    cats AS (
        SELECT * FROM (VALUES ('grocery_pos'),('gas_transport'),('restaurant'),('entertainment'),
                              ('health_fitness'),('travel'),('kids_pets'),('personal_care'),
                              ('home'),('shopping_pos'),('shopping_net'),('food_dining'),
                              ('misc_pos'),('misc_net'),('utilities')
        ) AS c(merchant_category)
    )
    INSERT INTO dbo.dim_fee_schedule (card_brand, merchant_category, interchange_pct, fixed_fee)
    SELECT b.card_brand, c.merchant_category,
           b.base_pct + (ABS(CHECKSUM(c.merchant_category)) % 50) * 0.0001,
           b.fixed_fee
    FROM brands b CROSS JOIN cats c;
END;
GO
