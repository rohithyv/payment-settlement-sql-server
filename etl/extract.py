"""Extract step: verify the source files and count source rows.

The source row count is captured up front so the load step can reconcile
source-vs-target at the end of the run.
"""
from pathlib import Path


def extract(csv_path: Path, merchants_path: Path) -> dict:
    if not csv_path.exists():
        raise FileNotFoundError(f"transaction file not found: {csv_path}")
    if not merchants_path.exists():
        raise FileNotFoundError(f"merchant master not found: {merchants_path}")

    with open(csv_path, "rb") as f:
        rows_read = sum(1 for _ in f) - 1  # minus header

    return {
        "csv_path": csv_path.resolve(),
        "merchants_path": merchants_path.resolve(),
        "rows_read": rows_read,
    }
