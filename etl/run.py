"""ETL orchestrator.

  python -m etl.run --full          [--csv data/transactions.csv]
  python -m etl.run --incremental   [--csv data/transactions_inc.csv]

--full:        (re)applies migrations, truncates the warehouse, resets the
               watermark, then loads the batch from scratch.
--incremental: loads only rows at/after the stored watermark; MERGE makes
               re-running the same batch a no-op.
"""
import argparse
import json
import sys
from pathlib import Path

from etl import config, db, extract, load, migrate, stage, transform, validate


def start_run(cn, mode: str, source_file: str) -> int:
    cur = cn.cursor()
    cur.execute(
        "INSERT INTO dbo.etl_run_log (run_mode, source_file) OUTPUT INSERTED.etl_run_id VALUES (?, ?)",
        mode, source_file)
    run_id = cur.fetchone()[0]
    cn.commit()
    return run_id


def finish_run(cn, run_id: int, status: str, counts: dict, message: str = "") -> None:
    cur = cn.cursor()
    cur.execute("""
        UPDATE dbo.etl_run_log
        SET ended_at = SYSUTCDATETIME(), rows_read = ?, rows_loaded = ?,
            rows_rejected = ?, status = ?, message = ?
        WHERE etl_run_id = ?
    """, counts.get("rows_read"), counts.get("loaded"), counts.get("rejected"),
        status, message[:2000], run_id)
    cn.commit()


def full_reset(cn) -> None:
    cur = cn.cursor()
    cur.execute("""
        TRUNCATE TABLE dbo.fact_transaction;
        DELETE FROM dbo.dim_card;
        DELETE FROM dbo.dim_merchant;
        TRUNCATE TABLE dbo.rejected_rows;
        UPDATE dbo.etl_watermark SET last_watermark = '1900-01-01',
               updated_at = SYSUTCDATETIME();
    """)
    cn.commit()


def run_pipeline(mode: str, csv_path: Path, merchants_path: Path,
                 database: str | None = None) -> dict:
    migrate.run_migrations(database)
    cn = db.raw_connection(database)
    try:
        if mode == "full":
            full_reset(cn)

        info = extract.extract(csv_path, merchants_path)
        run_id = start_run(cn, mode, str(info["csv_path"]))
        try:
            watermark = load.get_watermark(cn) if mode == "incremental" else None
            stage_counts = stage.stage(cn, info, watermark)
            pre_counts = validate.validate_pre(cn)
            dim_counts = transform.load_dimensions(cn)
            ri_counts = validate.validate_ri(cn)
            rejected = validate.persist_rejects(cn, run_id)
            loaded = load.load_fact(cn, run_id)
            load.advance_watermark(cn)

            recon = load.reconcile(cn, info["rows_read"], stage_counts["skipped_watermark"],
                                   loaded, rejected)
            status = "success" if recon["reconciled"] else "recon_failed"
            summary = {**recon, "run_id": run_id, "mode": mode,
                       "rule_counts": {**pre_counts, **ri_counts},
                       "dim_counts": dim_counts}
            finish_run(cn, run_id, status, recon,
                       json.dumps({"rules": {**pre_counts, **ri_counts}, **dim_counts}))
            return summary
        except Exception as exc:
            finish_run(cn, run_id, "failed", {}, str(exc))
            raise
    finally:
        cn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Payments warehouse ETL")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--full", action="store_true")
    g.add_argument("--incremental", action="store_true")
    p.add_argument("--csv", type=Path, default=None)
    p.add_argument("--merchants", type=Path, default=config.DATA_DIR / "merchants.csv")
    args = p.parse_args()

    mode = "full" if args.full else "incremental"
    default_csv = config.DATA_DIR / ("transactions.csv" if mode == "full"
                                     else "transactions_inc.csv")
    summary = run_pipeline(mode, args.csv or default_csv, args.merchants)

    print(json.dumps(summary, indent=2, default=str))
    if not summary["reconciled"]:
        print("RECONCILIATION FAILED", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
