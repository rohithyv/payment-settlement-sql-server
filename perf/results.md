# Tuning results — monthly fees by merchant category and region

| | elapsed (warm) | fact table access | execution mode | est. subtree cost |
|---|---|---|---|---|
| **Before** | 1,071 ms | Clustered Index Scan on `PK_fact_transaction` | Row | 24.8108 |
| **After**  | 96 ms | Index Scan on `IX_fact_fees_columnstore` | Batch | 1.98211 |

Speed-up: **11.1x**. Index build took 2,533 ms.

Plans saved to `plan_before.sqlplan` / `plan_after.sqlplan` — open them in SSMS
for the graphical view.
