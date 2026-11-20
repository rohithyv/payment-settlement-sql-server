-- Monthly settlement fees by merchant category and region.
-- Before tuning this forces a full scan of the clustered fact index.
SELECT d.year_month,
       m.merchant_category,
       r.region_name,
       COUNT_BIG(*)      AS txn_count,
       SUM(f.amount)     AS gross_amount,
       SUM(f.fee_amount) AS total_fees
FROM dbo.fact_transaction f
JOIN dbo.dim_date     d ON d.date_key     = f.date_key
JOIN dbo.dim_merchant m ON m.merchant_key = f.merchant_key
JOIN dbo.dim_region   r ON r.region_key   = f.region_key
GROUP BY d.year_month, m.merchant_category, r.region_name
ORDER BY d.year_month, total_fees DESC;
