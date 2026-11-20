"""Synthetic card-transaction generator (Sparkov / IBM TabFormer style).

Produces two CSVs:
  - a merchant master file (the trusted dimension source)
  - a transaction file referencing those merchants

Usage:
  python -m etl.generate --rows 1000000
  python -m etl.generate --rows 50000 --start 2026-07-01 --end 2026-08-24 \
      --out data/transactions_inc.csv --seed 7

A small percentage of dirty rows (nulls, duplicates, bad amounts/dates,
unknown merchant ids) is injected so the validation layer has work to do.
Scales to ~20M rows: writing is streamed, memory stays flat.
"""
import argparse
import csv
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from etl import config

CATEGORIES = [
    "grocery_pos", "gas_transport", "restaurant", "entertainment", "health_fitness",
    "travel", "kids_pets", "personal_care", "home", "shopping_pos", "shopping_net",
    "food_dining", "misc_pos", "misc_net", "utilities",
]
BRANDS = [("Visa", 0.5), ("Mastercard", 0.3), ("Amex", 0.12), ("Discover", 0.08)]
CARD_TYPES = ["credit", "debit"]
STATE_REGION = {
    "NY": "Northeast", "MA": "Northeast", "PA": "Northeast", "NJ": "Northeast",
    "IL": "Midwest", "OH": "Midwest", "MI": "Midwest", "MN": "Midwest", "WI": "Midwest",
    "TX": "South", "FL": "South", "GA": "South", "NC": "South", "TN": "South", "VA": "South",
    "CA": "West", "WA": "West", "OR": "West", "CO": "West", "AZ": "West", "NV": "West",
}
STATES = list(STATE_REGION)
CITY_BY_STATE = {
    "NY": "New York", "MA": "Boston", "PA": "Philadelphia", "NJ": "Newark",
    "IL": "Chicago", "OH": "Columbus", "MI": "Detroit", "MN": "Minneapolis", "WI": "Milwaukee",
    "TX": "Houston", "FL": "Miami", "GA": "Atlanta", "NC": "Charlotte", "TN": "Nashville",
    "VA": "Richmond", "CA": "Los Angeles", "WA": "Seattle", "OR": "Portland",
    "CO": "Denver", "AZ": "Phoenix", "NV": "Las Vegas",
}
NAME_PARTS_A = ["Summit", "Golden", "Blue", "Rapid", "Prime", "Urban", "Coastal", "Sunset",
                "Iron", "Cedar", "Silver", "Metro", "Royal", "Falcon", "Nova", "Atlas"]
NAME_PARTS_B = ["Mart", "Fuel", "Diner", "Cinema", "Gym", "Tours", "Toys", "Salon", "Depot",
                "Outlet", "Store", "Bistro", "Trading", "Goods", "Services", "Supply"]

TXN_HEADER = [
    "transaction_id", "transaction_ts", "card_number", "card_brand", "card_type",
    "merchant_id", "amount", "currency", "is_fraud",
]
MERCHANT_HEADER = ["merchant_id", "merchant_name", "merchant_category", "city", "state"]


def _pick_brand(rng: random.Random) -> str:
    r, acc = rng.random(), 0.0
    for brand, w in BRANDS:
        acc += w
        if r < acc:
            return brand
    return BRANDS[-1][0]


def make_merchants(rng: random.Random, n: int) -> list[dict]:
    merchants = []
    for i in range(n):
        state = rng.choice(STATES)
        merchants.append({
            "merchant_id": f"M{100000 + i}",
            "merchant_name": f"{rng.choice(NAME_PARTS_A)} {rng.choice(NAME_PARTS_B)} #{i}",
            "merchant_category": rng.choice(CATEGORIES),
            "city": CITY_BY_STATE[state],
            "state": state,
        })
    return merchants


def make_cards(rng: random.Random, n: int) -> list[tuple[str, str, str]]:
    cards = []
    for _ in range(n):
        brand = _pick_brand(rng)
        prefix = {"Visa": "4", "Mastercard": "5", "Amex": "37", "Discover": "6011"}[brand]
        pan = prefix + "".join(rng.choice("0123456789") for _ in range(16 - len(prefix)))
        cards.append((pan, brand, rng.choice(CARD_TYPES)))
    return cards


def generate(rows: int, out: Path, merchants_out: Path, start: datetime, end: datetime,
             seed: int = 42, dirty_pct: float = 0.005, n_merchants: int = 2000,
             n_cards: int = 100_000) -> None:
    rng = random.Random(seed)
    merchants = make_merchants(rng, n_merchants)
    cards = make_cards(rng, min(n_cards, max(1000, rows // 10)))
    span = (end - start).total_seconds()

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(merchants_out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(MERCHANT_HEADER)
        for m in merchants:
            w.writerow([m["merchant_id"], m["merchant_name"], m["merchant_category"],
                        m["city"], m["state"]])

    dup_buffer: list[list] = []
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(TXN_HEADER)
        for i in range(rows):
            ts = start + timedelta(seconds=rng.random() * span)
            pan, brand, ctype = cards[rng.randrange(len(cards))]
            m = merchants[rng.randrange(n_merchants)]
            amount = round(rng.lognormvariate(3.4, 1.1), 2)  # median ~$30, long tail
            row = [
                str(uuid.uuid4()),
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                pan, brand, ctype,
                m["merchant_id"],
                f"{amount:.2f}", "USD",
                "1" if rng.random() < 0.004 else "0",
            ]
            if rng.random() < dirty_pct:
                kind = rng.randrange(5)
                if kind == 0:      # null in a required column
                    row[rng.choice([0, 1, 2, 5, 6])] = ""
                elif kind == 1:    # duplicate transaction id (re-emit later)
                    dup_buffer.append(list(row))
                elif kind == 2:    # amount out of range
                    row[6] = rng.choice(["-5.00", "0.00", "1500000.00"])
                elif kind == 3:    # date out of range
                    row[1] = rng.choice(["1999-12-31 10:00:00", "2031-01-01 00:00:00"])
                else:              # unknown merchant -> referential integrity failure
                    row[5] = f"MBAD{rng.randrange(999)}"
            w.writerow(row)
            if dup_buffer and rng.random() < 0.3:
                w.writerow(dup_buffer.pop())
        for row in dup_buffer:
            w.writerow(row)


def main() -> None:
    p = argparse.ArgumentParser(description="Generate synthetic card transactions")
    p.add_argument("--rows", type=int, default=1_000_000, help="rows to generate (up to ~20M)")
    p.add_argument("--out", type=Path, default=config.DATA_DIR / "transactions.csv")
    p.add_argument("--merchants-out", type=Path, default=config.DATA_DIR / "merchants.csv")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2026-08-24")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dirty-pct", type=float, default=0.005)
    args = p.parse_args()

    generate(args.rows, args.out, args.merchants_out,
             datetime.fromisoformat(args.start), datetime.fromisoformat(args.end),
             seed=args.seed, dirty_pct=args.dirty_pct)
    print(f"wrote {args.rows} rows -> {args.out}")
    print(f"wrote merchant master -> {args.merchants_out}")


if __name__ == "__main__":
    main()
