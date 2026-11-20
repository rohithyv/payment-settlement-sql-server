"""Capture before/after execution plans and timings for the slow query.

  python -m perf.capture_perf

Runs the monthly-fees query without the covering index (dropping it first if
present), saves the actual plan + timing, applies perf/fix_index.sql, and
repeats. Outputs:
  perf/plan_before.sqlplan   (open in SSMS)
  perf/plan_after.sqlplan
  perf/results.md
"""
import re
import time
from pathlib import Path
from xml.etree import ElementTree

from etl import db

PERF_DIR = Path(__file__).resolve().parent
QUERY = (PERF_DIR / "slow_query.sql").read_text(encoding="utf-8")
NS = {"p": "http://schemas.microsoft.com/sqlserver/2004/07/showplan"}


def run_with_plan(cn) -> tuple[float, str]:
    """Execute the query with the actual plan captured; return (ms, plan_xml)."""
    cur = cn.cursor()
    cur.execute("SET STATISTICS XML ON")
    t0 = time.perf_counter()
    cur.execute(QUERY)
    plan_xml = ""
    while True:
        rows = cur.fetchall()
        if rows and isinstance(rows[0][0], str) and rows[0][0].startswith("<ShowPlanXML"):
            plan_xml = rows[0][0]
        if not cur.nextset():
            break
    elapsed_ms = (time.perf_counter() - t0) * 1000
    cur.execute("SET STATISTICS XML OFF")
    return elapsed_ms, plan_xml


def plan_stats(plan_xml: str) -> dict:
    """The operator that actually reads fact_transaction + its execution mode."""
    root = ElementTree.fromstring(plan_xml)
    access = ("?", "?", "?")
    for op in root.iter(f"{{{NS['p']}}}RelOp"):
        scan = op.find(f"{{{NS['p']}}}IndexScan")
        if scan is None:
            continue
        obj = scan.find(f"{{{NS['p']}}}Object")
        if obj is not None and obj.get("Table") == "[fact_transaction]":
            access = (op.get("PhysicalOp"),
                      (obj.get("Index") or "").strip("[]"),
                      op.get("EstimatedExecutionMode", "Row"))
            break
    stmt = root.find(f".//{{{NS['p']}}}StmtSimple")
    cost = stmt.get("StatementSubTreeCost") if stmt is not None else "?"
    return {"fact_access": access, "est_cost": cost}


def bench(cn, label: str, plan_file: Path) -> dict:
    cn.cursor().execute("DBCC FREEPROCCACHE WITH NO_INFOMSGS")
    run_with_plan(cn)                       # warm-up run (buffer pool warm)
    ms, plan_xml = run_with_plan(cn)        # measured run
    plan_file.write_text(plan_xml, encoding="utf-8")
    stats = plan_stats(plan_xml)
    print(f"{label}: {ms:,.0f} ms | fact access: {stats['fact_access']} "
          f"| est cost: {stats['est_cost']}")
    return {"ms": ms, **stats}


def main() -> None:
    cn = db.raw_connection(autocommit=True)
    cur = cn.cursor()

    for ix in ("IX_fact_fees_columnstore", "IX_fact_fees_covering"):
        cur.execute(f"""
            IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = '{ix}'
                       AND object_id = OBJECT_ID('dbo.fact_transaction'))
                DROP INDEX {ix} ON dbo.fact_transaction
        """)

    before = bench(cn, "BEFORE (base indexes only)", PERF_DIR / "plan_before.sqlplan")

    t0 = time.perf_counter()
    cur.execute((PERF_DIR / "fix_index.sql").read_text(encoding="utf-8"))
    index_ms = (time.perf_counter() - t0) * 1000
    print(f"created IX_fact_fees_columnstore in {index_ms:,.0f} ms")

    after = bench(cn, "AFTER  (columnstore index)", PERF_DIR / "plan_after.sqlplan")

    speedup = before["ms"] / after["ms"] if after["ms"] else float("inf")
    results = f"""# Tuning results — monthly fees by merchant category and region

| | elapsed (warm) | fact table access | execution mode | est. subtree cost |
|---|---|---|---|---|
| **Before** | {before['ms']:,.0f} ms | {before['fact_access'][0]} on `{before['fact_access'][1]}` | {before['fact_access'][2]} | {before['est_cost']} |
| **After**  | {after['ms']:,.0f} ms | {after['fact_access'][0]} on `{after['fact_access'][1]}` | {after['fact_access'][2]} | {after['est_cost']} |

Speed-up: **{speedup:.1f}x**. Index build took {index_ms:,.0f} ms.

Plans saved to `plan_before.sqlplan` / `plan_after.sqlplan` — open them in SSMS
for the graphical view.
"""
    (PERF_DIR / "results.md").write_text(results, encoding="utf-8")
    print("wrote perf/results.md")
    cn.close()


if __name__ == "__main__":
    main()
