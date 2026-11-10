"""
FiT cost forecasting: quarterly and annual levelisation cost by delivery year (1 April to 31 March).

How a quarter is forecast
-------------------------
1. Model cost at average weather: the bottom-up cost of every FiT installation still eligible
   (fit_portfolio.csv, at 2026/27 tariff rates), uplifted by the tariff indexation path
   (tariff_assumptions.csv) and split across quarters using the last fully actual year's
   quarterly pattern of Ofgem's levelisation fund.
2. Weather: the solar part of that quarter is multiplied by a solar weather index, the ratio of
   GB solar load factor in the relevant generation period to the average of the comparison years
   for the same days (from PV_Live). Levelisation payments lag generation, so by default the
   generation period is the previous quarter. Days already generated use actual solar; days still
   to come use the low (P10), central (P50) or high (P90) historic weather for that time of year.
3. Calibration: the ratio of Ofgem's actual levelisation fund to the weather-adjusted model over the
   last four published quarters. Every forecast quarter is multiplied by it, so when a quarter
   comes in over or under the forecast, the rest of the year and all later years move with it.
4. Published quarters use Ofgem's actual figure; a published annual levelisation replaces the sum
   of the quarters for that year.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import solar

APP_DIR = Path(__file__).parent
PORTFOLIO_FILE = APP_DIR / "fit_portfolio.csv"
QUARTERS_FILE = APP_DIR / "quarterly_actuals.csv"
ANNUAL_FILE = APP_DIR / "annual_levelisation.csv"
TARIFF_FILE = APP_DIR / "tariff_assumptions.csv"

BASE_YEAR = 2026          # fit_portfolio.csv is priced at 2026/27 tariff rates
QUARTER_NAMES = {1: "Q1 Apr–Jun", 2: "Q2 Jul–Sep", 3: "Q3 Oct–Dec", 4: "Q4 Jan–Mar"}
# Day-of-scheme-year index (0 = 1 April) at which each calendar quarter starts and ends (exclusive)
QUARTER_DAYS = {1: (0, 91), 2: (91, 183), 3: (183, 275), 4: (275, 365)}
CASES = ["Low", "Central", "High"]
CASE_PCT = {"Low": 10, "Central": 50, "High": 90}


# ---------------------------------------------------------------- inputs

def load_inputs() -> dict:
    num = lambda s: pd.to_numeric(s, errors="coerce")
    p = pd.read_csv(PORTFOLIO_FILE)
    q = pd.read_csv(QUARTERS_FILE)
    q["levelisation_fund_gbp_m"] = num(q["levelisation_fund_gbp_m"])
    a = pd.read_csv(ANNUAL_FILE) if ANNUAL_FILE.exists() else pd.DataFrame(columns=["year_start", "levelisation_fund_gbp_m"])
    a["levelisation_fund_gbp_m"] = num(a["levelisation_fund_gbp_m"])
    t = pd.read_csv(TARIFF_FILE)
    t["tariff_uplift_pct"] = num(t["tariff_uplift_pct"])
    return {"portfolio": p, "quarters": q, "annual": a, "tariff": t}


def portfolio_by_year(p: pd.DataFrame) -> pd.DataFrame:
    """Cost at 2026/27 rates and average weather, split solar / other, by delivery year."""
    p = p.assign(cost=p["generation_payments_gbp_m"] + p["export_payments_gbp_m"],
                 is_pv=p["technology"].eq("Photovoltaic"))
    g = p.groupby(["year_start", "is_pv"])["cost"].sum().unstack(fill_value=0)
    return pd.DataFrame({"pv": g.get(True, 0.0), "other": g.get(False, 0.0)})


def tariff_index(t: pd.DataFrame, years) -> dict:
    """Tariff level relative to 2026/27 (= 1). Uplift in year y applies from 1 April of y."""
    up = dict(zip(t["year_start"].astype(int), t["tariff_uplift_pct"].fillna(0) / 100))
    last = up[max(up)] if up else 0.0
    idx = {BASE_YEAR: 1.0}
    for y in range(BASE_YEAR + 1, max(years) + 1):
        idx[y] = idx[y - 1] * (1 + up.get(y, last))
    for y in range(BASE_YEAR - 1, min(years) - 1, -1):
        idx[y] = idx[y + 1] / (1 + up.get(y + 1, 0.0))
    return idx


def quarter_shares(q: pd.DataFrame) -> tuple[np.ndarray, str]:
    """Quarterly split from the last year with four actual quarters."""
    act = q[q["status"].eq("actual") & q["levelisation_fund_gbp_m"].notna()]
    full = act.groupby("year_start").filter(lambda g: len(g) == 4)
    if full.empty:
        return np.array([0.28, 0.31, 0.21, 0.20]), "default"
    y = full["year_start"].max()
    v = full[full["year_start"] == y].sort_values("quarter")["levelisation_fund_gbp_m"].to_numpy()
    return v / v.sum(), solar.label(int(y))


# ---------------------------------------------------------------- solar weather

class Weather:
    """Solar weather indices by generation period, from PV_Live daily data."""

    def __init__(self, solar_df: pd.DataFrame, lookback: int = 10):
        d = solar.add_calendar(solar_df)
        self.lf = d.pivot_table(index="day", columns="fy", values="load_factor").reindex(range(365))
        self.current_fy = int(d["fy"].iloc[-1])
        self.years = [y for y in range(self.current_fy - lookback, self.current_fy)
                      if y in self.lf.columns and self.lf[y].notna().sum() >= 360]
        self.baseline = self.lf[self.years].mean(axis=1)       # average load factor per day of year
        # Each comparison year's weather index for each calendar quarter
        self.hist = pd.DataFrame({cq: [self._ratio(self.lf[y], cq) for y in self.years]
                                  for cq in QUARTER_DAYS}, index=self.years)
        self.factors = {c: {cq: float(np.percentile(self.hist[cq], CASE_PCT[c])) for cq in QUARTER_DAYS}
                        for c in CASES}

    def _ratio(self, series: pd.Series, cq: int) -> float:
        a, b = QUARTER_DAYS[cq]
        return float(series.iloc[a:b].sum() / self.baseline.iloc[a:b].sum())

    def index(self, fy: int, cq: int, case: str) -> tuple[float, int]:
        """Weather index for calendar quarter cq of scheme year fy, and how many of its days are actual.
        Actual days use measured solar; remaining days use the case's historic factor for that quarter."""
        a, b = QUARTER_DAYS[cq]
        base = self.baseline.iloc[a:b]
        actual = self.lf[fy].iloc[a:b] if fy in self.lf.columns else pd.Series(np.nan, index=base.index)
        known = actual.notna()
        num = actual[known].sum() + (base[~known] * self.factors[case][cq]).sum()
        return float(num / base.sum()), int(known.sum())


def generation_period(year: int, quarter: int, lag: int) -> tuple[int, int]:
    """The scheme year and calendar quarter whose generation a levelisation quarter pays for."""
    k = year * 4 + (quarter - 1) - lag
    return k // 4, k % 4 + 1


# ---------------------------------------------------------------- forecast

def forecast(inputs: dict, weather: Weather, lag: int = 1, calibrate: bool = True,
             start_year: int = BASE_YEAR, years: int = 6) -> dict:
    p, q, a, t = inputs["portfolio"], inputs["quarters"], inputs["annual"], inputs["tariff"]
    pf = portfolio_by_year(p)
    yrs = list(range(start_year, start_year + years))
    hist_years = sorted(q["year_start"].astype(int).unique())
    idx = tariff_index(t, list(range(min(hist_years + yrs), max(yrs) + 1)))
    shares, profile_year = quarter_shares(q)

    def model(y: int, qtr: int, w: float) -> float:
        base = pf.loc[y] if y in pf.index else pf.loc[pf.index.min()]   # years before 2026/27 use 2026/27 eligibility
        total = (base["pv"] + base["other"]) * idx[y] * shares[qtr - 1]
        other = base["other"] * idx[y] / 4
        return other + (total - other) * w

    # Calibration on the last four actual quarters, at the weather each was generated in
    act = q[q["status"].eq("actual") & q["levelisation_fund_gbp_m"].notna()].copy()
    act = act.sort_values(["year_start", "quarter"]).tail(4)
    cal_rows = []
    for _, r in act.iterrows():
        fy, cq = generation_period(int(r.year_start), int(r.quarter), lag)
        w, _ = weather.index(fy, cq, "Central")
        cal_rows.append({"Quarter": f"{solar.label(int(r.year_start))} {QUARTER_NAMES[int(r.quarter)]}",
                         "Actual £m": r.levelisation_fund_gbp_m, "Solar weather index": w,
                         "Model at that weather £m": model(int(r.year_start), int(r.quarter), w)})
    cal_tbl = pd.DataFrame(cal_rows)
    calibration = (cal_tbl["Actual £m"].sum() / cal_tbl["Model at that weather £m"].sum()) if calibrate and len(cal_tbl) == 4 else 1.0

    rows = []
    for y in yrs:
        for qtr in range(1, 5):
            rec = q[(q["year_start"] == y) & (q["quarter"] == qtr)]
            actual = rec["levelisation_fund_gbp_m"].iloc[0] if len(rec) and rec["status"].iloc[0] == "actual" else np.nan
            fy, cq = generation_period(y, qtr, lag)
            for case in CASES:
                w, known = weather.index(fy, cq, case)
                fc = model(y, qtr, w) * calibration
                rows.append({"year_start": y, "Delivery year": solar.label(y), "quarter": qtr, "Quarter": QUARTER_NAMES[qtr],
                             "Case": case, "Status": "Actual" if pd.notna(actual) else "Forecast",
                             "Cost £m": actual if pd.notna(actual) else fc, "Forecast £m": fc,
                             "Average-weather model £m": model(y, qtr, 1.0),
                             "Weather index": w, "Actual solar days": known,
                             "Generation period": f"{solar.label(fy)} {QUARTER_NAMES[cq][3:]}"})
    qdf = pd.DataFrame(rows)

    annual = qdf.groupby(["year_start", "Delivery year", "Case"], as_index=False).agg(
        **{"Cost £m": ("Cost £m", "sum"), "Quarters actual": ("Status", lambda s: int((s == "Actual").sum()))})
    annual["Source"] = np.select(
        [annual["Quarters actual"] == 4, annual["Quarters actual"] > 0],
        ["Ofgem quarterly actuals", annual["Quarters actual"].astype(str) + " actual quarters + forecast"],
        default="Forecast")
    for _, r in a.dropna(subset=["levelisation_fund_gbp_m"]).iterrows():
        m = annual["year_start"] == int(r.year_start)
        annual.loc[m, "Cost £m"] = r.levelisation_fund_gbp_m
        annual.loc[m, "Source"] = "Ofgem annual levelisation"

    return {"quarters": qdf, "annual": annual, "calibration": calibration, "calibration_table": cal_tbl,
            "shares": shares, "profile_year": profile_year, "tariff_index": idx,
            "weather_factors": pd.DataFrame(weather.factors).T, "weather_years": weather.years}


def wide_table(fc: dict, case: str) -> pd.DataFrame:
    """Delivery years down the side, Q1–Q4 and the annual total across."""
    q = fc["quarters"][fc["quarters"]["Case"] == case]
    w = q.pivot(index="Delivery year", columns="Quarter", values="Cost £m")[list(QUARTER_NAMES.values())]
    status = q.pivot(index="Delivery year", columns="Quarter", values="Status")[list(QUARTER_NAMES.values())]
    ann = fc["annual"][fc["annual"]["Case"] == case].set_index("Delivery year")
    w["Annual total"] = ann["Cost £m"]
    w["Basis"] = ann["Source"]
    return w, status


def annual_cases(fc: dict) -> pd.DataFrame:
    a = fc["annual"].pivot(index="Delivery year", columns="Case", values="Cost £m")[CASES]
    a["Basis"] = fc["annual"][fc["annual"]["Case"] == "Central"].set_index("Delivery year")["Source"]
    return a
