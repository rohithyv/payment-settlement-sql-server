-- Tuning fix: partition-aligned nonclustered COLUMNSTORE index over exactly
-- the columns the monthly-fees aggregation touches. The optimizer switches
-- from a full clustered-index scan in row mode to a columnstore scan in
-- batch mode, with aggregate pushdown — the right index shape for
-- large GROUP BY / SUM analytical queries.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_fact_fees_columnstore'
                 AND object_id = OBJECT_ID('dbo.fact_transaction'))
CREATE NONCLUSTERED COLUMNSTORE INDEX IX_fact_fees_columnstore
    ON dbo.fact_transaction (date_key, merchant_key, region_key, amount, fee_amount)
    ON ps_txn_month (transaction_date);
