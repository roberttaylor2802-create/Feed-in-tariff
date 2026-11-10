"""
GB gross metered demand, exemptions and FiT liable demand, by quarter and delivery year.

Sources
-------
- demand_quarterly.csv: Ofgem's FIT quarterly reports give, for every periodic levelisation, the
  electricity licensed suppliers reported supplying in the quarter (metered supply, the FiT
  levelisation base) and how much of it was exempt for Energy Intensive Industries. Add each new
  quarter as Ofgem publishes it, alongside that quarter's levelisation fund.
- demand_annual_history.csv: earlier years' annual gross supply, from DESNZ's RO level calculations
  (Ofgem RO supply outturn + EII exempt), scaled to the Ofgem FIT quarterly basis.
- exemptions.csv: forecasts of EII and BICS exempt volumes (DBT/BIST) and any Class A or other
  exempt supply, by delivery year.

Liable demand = gross metered supply − EII − BICS × take-up − Class A − other exemptions.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import solar

APP_DIR = Path(__file__).parent
QUARTERLY_FILE = APP_DIR / "demand_quarterly.csv"
ANNUAL_FILE = APP_DIR / "demand_annual_history.csv"
EXEMPTIONS_FILE = APP_DIR / "exemptions.csv"

METHODS = ["Holt damped trend", "Post-crisis trend (3-yr CAGR)", "Linear trend (5-yr OLS)", "5-yr CAGR", "DESNZ EEP trend"]
QUARTER_NAMES = {1: "Q1 Apr–Jun", 2: "Q2 Jul–Sep", 3: "Q3 Oct–Dec", 4: "Q4 Jan–Mar"}


def load_inputs() -> dict:
    num = lambda s: pd.to_numeric(s, errors="coerce")
    q = pd.read_csv(QUARTERLY_FILE)
    for c in ("supplied_twh", "eii_exempt_twh", "bics_exempt_twh"):
        q[c] = num(q[c])
    a = pd.read_csv(ANNUAL_FILE)
    a["gross_twh"] = num(a["gross_twh"])
    e = pd.read_csv(EXEMPTIONS_FILE)
    for c in ("eii_twh", "bics_full_takeup_twh", "class_a_twh", "other_twh"):
        e[c] = num(e[c])
    return {"quarterly": q, "annual": a, "exemptions": e}


# ---------------------------------------------------------------- forecasting methods

def holt_damped(y: np.ndarray, h: int) -> np.ndarray:
    """Holt's linear trend with damping; smoothing and damping parameters fitted by least squares."""
    best = None
    for alpha in np.arange(0.05, 1.0001, 0.05):
        for beta in np.arange(0.05, 1.0001, 0.05):
            for phi in np.arange(0.80, 0.9801, 0.02):
                level, trend, sse = y[0], y[1] - y[0], 0.0
                for v in y[1:]:
                    f = level + phi * trend
                    sse += (v - f) ** 2
                    new = alpha * v + (1 - alpha) * (level + phi * trend)
                    trend = beta * (new - level) + (1 - beta) * phi * trend
                    level = new
                if best is None or sse < best[0]:
                    best = (sse, level, trend, phi)
    _, level, trend, phi = best
    return np.array([level + trend * sum(phi ** k for k in range(1, i + 1)) for i in range(1, h + 1)])


def method_forecast(y: np.ndarray, method: str, h: int) -> np.ndarray:
    n = len(y)
    steps = np.arange(1, h + 1)
    if method == "Holt damped trend":
        return holt_damped(y, h)
    if method == "Post-crisis trend (3-yr CAGR)":
        return y[-1] * (y[-1] / y[-3]) ** (steps / 2)
    if method == "Linear trend (5-yr OLS)":
        x = np.arange(n)
        slope, intercept = np.polyfit(x, y, 1)
        return intercept + slope * (n - 1 + steps)
    if method == "5-yr CAGR":
        return y[-1] * (y[-1] / y[0]) ** (steps / (n - 1))
    if method == "DESNZ EEP trend":   # DESNZ applied +4.0% over two years in its RO 2027/28 calculation
        return y[-1] * 1.04 ** (steps / 2)
    raise ValueError(method)


# ---------------------------------------------------------------- history

def annual_history(inp: dict) -> pd.DataFrame:
    """Annual gross supply and EII: Ofgem quarterly sums where all four quarters exist, else the
    annual history file. Estimated quarters are flagged."""
    q = inp["quarterly"].dropna(subset=["supplied_twh"])
    rows = []
    for y, g in q.groupby("year_start"):
        if len(g) == 4:
            rows.append({"year_start": int(y), "gross_twh": g["supplied_twh"].sum(), "eii_twh": g["eii_exempt_twh"].sum(),
                         "basis": "Ofgem FIT quarterly reports" + (" (includes an estimated quarter)" if (g["status"] != "actual").any() else "")})
    have = {r["year_start"] for r in rows}
    for _, r in inp["annual"].iterrows():
        if int(r.year_start) not in have:
            rows.append({"year_start": int(r.year_start), "gross_twh": r.gross_twh, "eii_twh": r.get("eii_twh", np.nan), "basis": r.basis})
    h = pd.DataFrame(rows).sort_values("year_start").reset_index(drop=True)
    h["Delivery year"] = h["year_start"].map(solar.label)
    return h


def quarter_profile(inp: dict) -> tuple[np.ndarray, np.ndarray]:
    """Average share of the year's supply and EII exempt volume in each quarter, over full years."""
    q = inp["quarterly"].dropna(subset=["supplied_twh"])
    full = q.groupby("year_start").filter(lambda g: len(g) == 4)
    if full.empty:
        return np.full(4, 0.25), np.full(4, 0.25)
    piv = full.pivot(index="year_start", columns="quarter", values="supplied_twh")
    eii = full.pivot(index="year_start", columns="quarter", values="eii_exempt_twh")
    s = (piv.div(piv.sum(axis=1), axis=0)).mean().to_numpy()
    e = (eii.div(eii.sum(axis=1), axis=0)).mean().to_numpy()
    return s / s.sum(), e / e.sum()


# ---------------------------------------------------------------- forecast

def forecast(inp: dict, method: str, bics_takeup: float = 1.0, carry_variance: bool = True,
             start_year: int = 2026, years: int = 6, history_years: int = 5) -> dict:
    hist = annual_history(inp)
    past = hist[hist["year_start"] < start_year].tail(history_years)
    y = past["gross_twh"].to_numpy()
    yrs = list(range(start_year, start_year + years))
    shares, eii_shares = quarter_profile(inp)

    # Annual gross forecast for every method (for comparison) and the chosen one
    by_method = {m: method_forecast(y, m, years) for m in METHODS}
    gross_annual = dict(zip(yrs, by_method[method]))

    # Year-to-date variance in the current year: actual quarters ÷ forecast for those quarters
    q = inp["quarterly"]
    cur = q[(q["year_start"] == start_year) & q["supplied_twh"].notna() & q["status"].eq("actual")]
    fc_ytd = sum(gross_annual[start_year] * shares[int(r.quarter) - 1] for _, r in cur.iterrows())
    variance = cur["supplied_twh"].sum() / fc_ytd if len(cur) else 1.0

    # Exemption forecasts
    ex = inp["exemptions"].set_index("year_start")
    rows = []
    prev = {"gross": y[-1], "eii": None, "bics": None}
    last_eii = past["eii_twh"].dropna().iloc[-1] if past["eii_twh"].notna().any() else 0.0
    for yr in yrs:
        g_ann = gross_annual[yr] * (variance if carry_variance else 1.0)
        growth = g_ann / prev["gross"]
        e_row = ex.loc[yr] if yr in ex.index else pd.Series(dtype=float)
        eii_ann = e_row.get("eii_twh", np.nan)
        eii_ann = eii_ann if pd.notna(eii_ann) else (prev["eii"] if prev["eii"] is not None else last_eii) * growth
        bics_full = e_row.get("bics_full_takeup_twh", np.nan)
        bics_full = bics_full if pd.notna(bics_full) else ((prev["bics"] or 0.0) * growth)
        class_a = e_row.get("class_a_twh", 0.0) if pd.notna(e_row.get("class_a_twh", np.nan)) else 0.0
        other = e_row.get("other_twh", 0.0) if pd.notna(e_row.get("other_twh", np.nan)) else 0.0
        prev = {"gross": g_ann, "eii": eii_ann, "bics": bics_full}
        for qt in range(1, 5):
            rec = q[(q["year_start"] == yr) & (q["quarter"] == qt)]
            actual = len(rec) and pd.notna(rec["supplied_twh"].iloc[0]) and rec["status"].iloc[0] == "actual"
            if actual:
                gq = rec["supplied_twh"].iloc[0]
                eq = rec["eii_exempt_twh"].iloc[0]
                bq = rec["bics_exempt_twh"].iloc[0] if pd.notna(rec["bics_exempt_twh"].iloc[0]) else bics_full * bics_takeup / 4
            else:
                g_q = gross_annual[yr] * shares[qt - 1]
                gq = g_q * (variance if (carry_variance or yr == start_year) else 1.0)
                eq = eii_ann * eii_shares[qt - 1]
                bq = bics_full * bics_takeup / 4
            ca, ot = class_a / 4, other / 4
            rows.append({"year_start": yr, "Delivery year": solar.label(yr), "quarter": qt, "Quarter": QUARTER_NAMES[qt],
                         "Status": "Actual" if actual else "Forecast",
                         "Gross TWh": gq, "EII TWh": eq, "BICS TWh": bq, "Class A TWh": ca, "Other TWh": ot,
                         "Liable TWh": gq - eq - bq - ca - ot})
    qdf = pd.DataFrame(rows)
    annual = qdf.groupby(["year_start", "Delivery year"], as_index=False)[
        ["Gross TWh", "EII TWh", "BICS TWh", "Class A TWh", "Other TWh", "Liable TWh"]].sum()
    annual["Quarters actual"] = qdf.groupby("year_start")["Status"].apply(lambda s: (s == "Actual").sum()).to_numpy()

    methods = pd.DataFrame(by_method, index=[solar.label(v) for v in yrs])
    return {"quarters": qdf, "annual": annual, "history": hist, "methods": methods, "variance": variance,
            "shares": shares, "eii_shares": eii_shares, "history_used": past}


def liable_by_year(fc: dict) -> dict:
    return dict(zip(fc["annual"]["year_start"], fc["annual"]["Liable TWh"]))


def liable_by_quarter(fc: dict) -> dict:
    return {(int(r.year_start), int(r.quarter)): r["Liable TWh"] for _, r in fc["quarters"].iterrows()}


def historic_liable(inp: dict) -> dict:
    """Liable demand for past years from Ofgem quarterly reports (supply − EII)."""
    h = annual_history(inp)
    return dict(zip(h["year_start"], h["gross_twh"] - h["eii_twh"]))
