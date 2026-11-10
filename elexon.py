"""
Licence-exempt (Class A) supply volumes from Elexon.

Since BSC Modification P442 (live 27 February 2025), Exempt Supply Notification Agents report the
volume of licence-exempt supply behind each import meter, and Elexon publishes the total for each
settlement date as Total Exempt Supply Volume (S0621):

    GET https://data.elexon.co.uk/bmrs/api/v1/saa/datasets/total-exempt-volume/{settlementDate}

This module downloads it day by day, keeps the most mature settlement run for each period, and
saves daily totals to class_a_daily.csv (columns: date, exempt_mwh, run).

The response layout is read defensively: any numeric field whose name contains "volume" is treated
as the exempt volume in MWh. Check the first download against Elexon's portal.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd
import requests

APP_DIR = Path(__file__).parent
DAILY_FILE = APP_DIR / "class_a_daily.csv"
URL = "https://data.elexon.co.uk/bmrs/api/v1/saa/datasets/total-exempt-volume/{date}"
P442_START = dt.date(2025, 2, 27)
# Most to least mature settlement runs
RUN_ORDER = ["DF", "RF", "R3", "R2", "R1", "SF", "II"]


def _records(js) -> list[dict]:
    if isinstance(js, dict):
        for key in ("data", "Data", "items"):
            if isinstance(js.get(key), list):
                return js[key]
        return [js]
    return js if isinstance(js, list) else []


def _volume_field(rec: dict) -> str | None:
    for k, v in rec.items():
        if "volume" in k.lower() and isinstance(v, (int, float)):
            return k
    return None


def fetch_day(day: dt.date, session: requests.Session | None = None) -> dict | None:
    """Total exempt supply volume (MWh) for one settlement date, or None if Elexon has no data."""
    s = session or requests
    r = s.get(URL.format(date=day.isoformat()), params={"format": "json"}, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    recs = _records(r.json())
    if not recs:
        return None
    df = pd.DataFrame(recs)
    vol = _volume_field(recs[0])
    if vol is None:
        raise ValueError(f"No volume field found in Elexon response; fields: {list(recs[0])}")
    run_col = next((c for c in df.columns if c.lower() == "settlementruntype"), None)
    period_col = next((c for c in df.columns if c.lower() == "settlementperiod"), None)
    run = "n/a"
    if run_col:
        df["_rank"] = df[run_col].map({r: i for i, r in enumerate(RUN_ORDER)}).fillna(len(RUN_ORDER))
        keys = [period_col] if period_col else []
        df = df.sort_values("_rank").drop_duplicates(keys or None, keep="first") if keys else df[df["_rank"] == df["_rank"].min()]
        run = ",".join(sorted(df[run_col].astype(str).unique()))
    return {"date": day, "exempt_mwh": float(df[vol].sum()), "run": run}


def load_daily() -> pd.DataFrame:
    if not DAILY_FILE.exists():
        return pd.DataFrame(columns=["date", "exempt_mwh", "run"])
    d = pd.read_csv(DAILY_FILE, parse_dates=["date"])
    return d


def update(until: dt.date | None = None, refresh_days: int = 120, progress=None) -> int:
    """Download days missing from class_a_daily.csv up to `until` (default: 5 days ago), and re-download
    the last `refresh_days` so later settlement runs replace earlier ones. Returns days written."""
    until = until or dt.date.today() - dt.timedelta(days=5)
    have = load_daily()
    start = P442_START
    if len(have):
        start = max(P442_START, have["date"].max().date() - dt.timedelta(days=refresh_days))
    days = [start + dt.timedelta(days=i) for i in range((until - start).days + 1)]
    rows, sess = [], requests.Session()
    for i, d in enumerate(days):
        rec = fetch_day(d, sess)
        if rec:
            rows.append(rec)
        if progress:
            progress((i + 1) / len(days))
        time.sleep(0.05)
    new = pd.DataFrame(rows)
    if len(new):
        new["date"] = pd.to_datetime(new["date"])
        out = pd.concat([have, new]).drop_duplicates("date", keep="last").sort_values("date")
        out.assign(date=out["date"].dt.date).to_csv(DAILY_FILE, index=False)
    return len(new)


def quarterly(daily: pd.DataFrame) -> pd.DataFrame:
    """Exempt supply by delivery-year quarter, in TWh, with the number of days covered."""
    if daily.empty:
        return pd.DataFrame(columns=["year_start", "quarter", "class_a_twh", "days", "complete"])
    d = daily.copy()
    d["year_start"] = d["date"].map(lambda x: x.year if x.month >= 4 else x.year - 1)
    d["quarter"] = d["date"].dt.month.map(lambda m: {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3}.get(m, 4))
    g = d.groupby(["year_start", "quarter"]).agg(class_a_twh=("exempt_mwh", lambda s: s.sum() / 1e6),
                                                  days=("exempt_mwh", "size")).reset_index()
    span = {1: 91, 2: 92, 3: 92, 4: 90}
    g["complete"] = g.apply(lambda r: r["days"] >= span[int(r["quarter"])] - 1, axis=1)
    return g
