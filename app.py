"""
FiT forecasting dashboard.

Tabs:
  1. Solar Generation Forecasting – GB solar against previous years, and weather scenarios.
  2. FiT Cost Forecasting – quarterly and annual levelisation cost for the next six delivery years,
     updated by Ofgem's published quarters, with low / central / high solar weather cases.

Run:  streamlit run app.py
"""
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

import fit
import solar

TEAL = "#005763"
BLUE = "#00DED4"
GREY = "#CFD2D3"
ASH = "#656F7A"
FONT = dict(family="Arial, Helvetica, sans-serif")

st.set_page_config(page_title="FiT forecasting", layout="wide")


# ---------------------------------------------------------------- data

@st.cache_data(show_spinner=False)
def load_solar(_mtime: float) -> pd.DataFrame:
    return solar.load_daily()


if not solar.DATA_FILE.exists():
    st.error("The solar data file `solar_daily.csv` is missing. Add it to the repository next to `app.py`, "
             "or download it now. On Streamlit Cloud a downloaded file is lost when the app restarts.")
    if st.button("Download from PV_Live now (takes a few minutes)"):
        try:
            with st.spinner("Downloading April 2016 to yesterday from PV_Live…"):
                solar.create_from_pvlive()
            st.cache_data.clear()
            st.rerun()
        except requests.RequestException as e:
            st.error(f"Couldn’t reach api.pvlive.uk ({e.__class__.__name__}).")
    st.stop()

with st.sidebar:
    st.header("Data")
    if st.button("Update solar from PV_Live", help="Downloads any days missing since the last saved date."):
        try:
            with st.spinner("Downloading from PV_Live…"):
                added = solar.update_from_pvlive()
            st.success(f"Added {added} days." if added else "Already up to date.")
            st.cache_data.clear()
        except requests.RequestException as e:
            st.error("Couldn’t reach api.pvlive.uk. Check your network allows it. "
                     f"({e.__class__.__name__})")

df = load_solar(solar.DATA_FILE.stat().st_mtime)

with st.sidebar:
    st.caption(f"Solar: Sheffield Solar PV_Live, {df['date'].min():%d %b %Y} to {df['date'].max():%d %b %Y}.")

st.title("FiT forecasting")
tab_solar, tab_fit = st.tabs(["Solar Generation Forecasting", "FiT Cost Forecasting"])


# ================================================================ Solar tab

def band_chart(frame, title, y_title, band, years_n, fy_label):
    lo, hi = ("lower_2sd", "upper_2sd") if band.startswith("Mean") else ("min", "max")
    band_name = "Mean ± 2 sd" if band.startswith("Mean") else "Historic min to max"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=frame["day"], y=frame[hi], mode="lines", line=dict(width=0),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=frame["day"], y=frame[lo], mode="lines", line=dict(width=0),
                             fill="tonexty", fillcolor="rgba(0,222,212,0.22)", name=band_name,
                             customdata=frame[[hi, "date_label"]],
                             hovertemplate="%{customdata[1]}<br>Band %{y:,.1f} to %{customdata[0]:,.1f} GWh<extra></extra>"))
    fig.add_trace(go.Scatter(x=frame["day"], y=frame["mean"], mode="lines", name=f"Historic mean ({years_n} years)",
                             line=dict(color=TEAL, width=1.5, dash="dash"), customdata=frame["date_label"],
                             hovertemplate="%{customdata}<br>Mean %{y:,.1f} GWh<extra></extra>"))
    fig.add_trace(go.Scatter(x=frame["day"], y=frame["current"], mode="lines", name=fy_label,
                             line=dict(color=TEAL, width=2.5), customdata=frame["date_label"],
                             hovertemplate="%{customdata}<br>" + fy_label + " %{y:,.1f} GWh<extra></extra>"))
    fig.update_layout(
        title=dict(text=title, font=dict(size=16)), height=380, margin=dict(l=10, r=10, t=50, b=10),
        font=FONT, hovermode="x unified", plot_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        xaxis=dict(tickvals=[d for d, _ in solar.MONTH_TICKS], ticktext=[m for _, m in solar.MONTH_TICKS],
                   range=[0, 364], showgrid=False, linecolor=GREY),
        yaxis=dict(title=y_title, gridcolor="#EEF0F1", rangemode="tozero"),
    )
    return fig


with tab_solar:
    c = st.columns(4)
    lookback = c[0].radio("Compare with", [10, 5], format_func=lambda n: f"Previous {n} years", key="s_lb")
    band = c[1].radio("Band", ["Min to max", "Mean ± 2 standard deviations"], key="s_band")
    measure = c[2].radio("Measure", ["At today’s capacity", "Actual GWh"], key="s_meas",
                         help="“At today’s capacity” rescales each past year’s load factor to today’s installed "
                              "capacity, so the band shows weather rather than growth in panels.")
    smooth = c[3].radio("Smoothing", ["7-day average", "Daily"], key="s_sm")

    b = solar.bands(df, lookback, "adjusted" if measure.startswith("At") else "actual", 7 if smooth.startswith("7") else 1)
    fy_label = solar.label(b["current_fy"])
    st.subheader(f"GB solar generation, {fy_label}")
    s = solar.ytd_summary(b)
    if s:
        m = st.columns(3)
        m[0].metric(f"Solar since 1 April to {b['last_date'].day} {b['last_date']:%b}", f"{s['ytd_gwh']:,.0f} GWh")
        m[1].metric("Against the historic average", f"{s['vs_mean_pct']:+.1f}%",
                    help="Cumulative generation since 1 April against the comparison years’ average for the same days.")
        m[2].metric("Position in historic range", f"{s['range_position_pct']:.0f}%",
                    help="0% = the lowest comparison year to date, 100% = the highest. Outside 0–100% is a new low or high.")
    st.plotly_chart(band_chart(b["daily"], "Daily generation", "GWh per day", band, len(b["years"]), fy_label), width="stretch")
    st.plotly_chart(band_chart(b["cumulative"], "Cumulative generation from 1 April", "GWh", band, len(b["years"]), fy_label),
                    width="stretch")
    st.caption(f"Comparing {len(b['years'])} years: {', '.join(solar.label(y) for y in b['years'])}. "
               "PV_Live excludes solar sites in the Balancing Mechanism. 29 February is dropped so leap years line up.")

    st.subheader("Weather scenarios")
    cc1, cc2 = st.columns([2, 1])
    method = cc1.radio("Low / central / high", list(solar.SCENARIO_METHODS), horizontal=True, key="s_meth",
                       help="P10/P50/P90 take the 10th, 50th and 90th percentiles of the comparison years.")
    growth = cc2.number_input("Capacity growth next year (%)", value=0.0, step=1.0, key="s_gr",
                              help="GB solar capacity keeps growing; FiT capacity does not. Leave at 0% for the FiT model.")
    sc = solar.weather_scenarios(df, lookback, method, growth)
    st.write(f"Every comparison year’s weather replayed at today’s installed capacity ({sc.attrs['capacity_mwp']:,.0f} MWp). "
             f"{fy_label} adds each year’s weather for the remaining {sc.attrs['rest_days']} days to the "
             f"{sc.attrs['ytd_gwh']:,.0f} GWh generated to {sc.attrs['last_date'].day} {sc.attrs['last_date']:%B}.")
    rows_fmt = {r: ("{:,.0f}" if "GWh" in r else "{:+.1f}%" if "vs average" in r else "{:.3f}" if "weather factor" in r
                    else "{:.1f}%" if "load factor" in r else "{}") for r in sc.columns}
    tbl = sc.T.copy()
    tbl.attrs = {}
    tbl = tbl.apply(lambda col: [rows_fmt[r].format(v) for r, v in zip(tbl.index, col)])
    tbl.index.name = "Measure"
    st.dataframe(tbl, width="stretch")
    with st.expander("Each comparison year"):
        yy = solar.year_by_year(df, lookback)
        st.dataframe(yy.style.format({"Full year at today's capacity (GWh)": "{:,.0f}", "Weather factor": "{:.3f}",
                                      "Rest-of-year weather factor": "{:.3f}"}), width="stretch", hide_index=True)


# ================================================================ FiT cost tab

def quarter_chart(fc: dict, case: str) -> go.Figure:
    q = fc["quarters"]
    cen = q[q["Case"] == case].reset_index(drop=True)
    lo = q[q["Case"] == "Low"]["Cost £m"].to_numpy()
    hi = q[q["Case"] == "High"]["Cost £m"].to_numpy()
    x = cen["Delivery year"] + " Q" + cen["quarter"].astype(str)
    colors = [TEAL if s == "Actual" else BLUE for s in cen["Status"]]
    fig = go.Figure(go.Bar(
        x=x, y=cen["Cost £m"], marker_color=colors, name=case,
        error_y=dict(type="data", symmetric=False, array=hi - cen["Cost £m"], arrayminus=cen["Cost £m"] - lo,
                     color=ASH, thickness=1.2, width=3),
        customdata=pd.DataFrame({"s": cen["Status"], "lo": lo, "hi": hi}),
        hovertemplate="%{x}<br>%{customdata[0]}: £%{y:,.1f}m<br>Low £%{customdata[1]:,.1f}m, "
                      "high £%{customdata[2]:,.1f}m<extra></extra>"))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), font=FONT, plot_bgcolor="white",
                      showlegend=False, yaxis=dict(title="£m", gridcolor="#EEF0F1", rangemode="tozero"),
                      xaxis=dict(tickangle=-45))
    return fig


with tab_fit:
    st.subheader("Levelisation cost by quarter, next six delivery years")
    c = st.columns(4)
    case = c[0].radio("Weather case", fit.CASES, index=1, horizontal=True, key="f_case",
                      help="Low / central / high = the 10th / 50th / 90th percentile of past solar weather for each "
                           "quarter. Low assumes poor weather in every forecast quarter, high good weather in every one.")
    lag = c[1].radio("Levelisation pays for", [1, 0], key="f_lag",
                     format_func=lambda n: "Previous quarter’s generation" if n else "Same quarter’s generation",
                     help="FiT payments follow meter readings, so each levelisation quarter mostly pays for the "
                          "previous quarter’s generation. This also lets actual solar feed the next quarter’s forecast.")
    calibrate = c[2].toggle("Calibrate to last 4 quarters", value=True, key="f_cal",
                            help="Scales the forecast by actual ÷ model over the last four published quarters, so a "
                                 "quarter coming in over or under forecast moves the rest of the year and later years.")
    w_lookback = c[3].radio("Weather history", [10, 5], key="f_lb", format_func=lambda n: f"Previous {n} years")

    # ------------------------------------------------ Ofgem figures and assumptions (feed the forecast)
    base = fit.load_inputs()
    with st.expander("Update with Ofgem levelisation and tariff assumptions"):
        st.write("Add each quarter’s levelisation fund as Ofgem publishes it (status “actual”), and the annual "
                 "levelisation once a year is reconciled. The forecast updates straight away. Edits here last for this "
                 "session only: download the CSV and upload it to GitHub to keep them.")
        e1, e2 = st.columns([3, 2])
        with e1:
            st.markdown("**Quarterly levelisation fund** (`quarterly_actuals.csv`)")
            qe = st.data_editor(base["quarters"], num_rows="dynamic", width="stretch", key="qe",
                                column_config={"status": st.column_config.SelectboxColumn(options=["actual", "estimate", "awaiting"]),
                                               "levelisation_fund_gbp_m": st.column_config.NumberColumn(format="%.1f")})
            st.download_button("Download quarterly_actuals.csv", qe.to_csv(index=False).encode(), "quarterly_actuals.csv", "text/csv")
        with e2:
            st.markdown("**Annual levelisation** (`annual_levelisation.csv`)")
            ae = st.data_editor(base["annual"], num_rows="dynamic", width="stretch", key="ae",
                                column_config={"year_start": st.column_config.NumberColumn(format="%d"),
                                               "levelisation_fund_gbp_m": st.column_config.NumberColumn(format="%.1f")})
            st.download_button("Download annual_levelisation.csv", ae.to_csv(index=False).encode(), "annual_levelisation.csv", "text/csv")
            st.markdown("**Tariff indexation** (`tariff_assumptions.csv`)")
            te = st.data_editor(base["tariff"], width="stretch", key="te", disabled=["scheme_year", "year_start"])
            st.download_button("Download tariff_assumptions.csv", te.to_csv(index=False).encode(), "tariff_assumptions.csv", "text/csv")

    def clean(d, cols):
        d = d.copy()
        for c in cols:
            if c in d:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        return d

    inp = {"portfolio": base["portfolio"],
           "quarters": clean(qe, ["year_start", "quarter", "levelisation_fund_gbp_m"]).dropna(subset=["year_start", "quarter"]),
           "annual": clean(ae, ["year_start", "levelisation_fund_gbp_m"]).dropna(subset=["year_start"]),
           "tariff": clean(te, ["year_start", "tariff_uplift_pct"])}
    inp["quarters"]["year_start"] = inp["quarters"]["year_start"].astype(int)
    inp["quarters"]["quarter"] = inp["quarters"]["quarter"].astype(int)
    weather = fit.Weather(df, w_lookback)
    fc = fit.forecast(inp, weather, lag, calibrate)

    ann = fit.annual_cases(fc)
    cur = ann.iloc[0]
    k = st.columns(4)
    k[0].metric(f"{ann.index[0]} outturn estimate ({case.lower()})", f"£{fc['annual'].query('Case == @case')['Cost £m'].iloc[0]:,.0f}m",
                help=cur["Basis"])
    k[1].metric(f"{ann.index[0]} range", f"£{cur['Low']:,.0f}m to £{cur['High']:,.0f}m")
    k[2].metric("Calibration factor", f"{fc['calibration']:.3f}" if calibrate else "Off",
                help="Actual ÷ weather-adjusted model over the last four published quarters.")
    six = fc["annual"].query("Case == @case")["Cost £m"].sum()
    k[3].metric(f"Six-year total ({case.lower()})", f"£{six:,.0f}m")

    st.plotly_chart(quarter_chart(fc, case), width="stretch")
    st.caption("Dark bars are Ofgem’s published levelisation fund; light bars are forecasts. Whiskers show the low to "
               "high weather range.")

    wide, status = fit.wide_table(fc, case)
    shown = wide.copy()
    for col in fit.QUARTER_NAMES.values():
        shown[col] = [f"£{v:,.1f}m" + (" (A)" if s == "Actual" else "") for v, s in zip(wide[col], status[col])]
    shown["Annual total"] = [f"£{v:,.1f}m" for v in wide["Annual total"]]
    st.markdown(f"**{case} case, £m.** (A) = Ofgem actual.")
    st.dataframe(shown, width="stretch")

    st.markdown("**Annual totals by weather case, £m**")
    st.dataframe(ann.style.format({c: "£{:,.0f}m" for c in fit.CASES}), width="stretch")

    st.download_button("Download quarterly forecast (all cases) as CSV",
                       fc["quarters"].round(3).to_csv(index=False).encode(), "fit_quarterly_forecast.csv", "text/csv")

    # ------------------------------------------------ method
    with st.expander("How the forecast works"):
        st.markdown(f"""
- **Bottom-up model.** Every installation on Ofgem’s FiT register (30 June 2026) costed at its 2026/27 tariff,
  with generation from DESNZ FiT load factors at average weather, until its eligibility ends (`fit_portfolio.csv`).
  Uplifted each April by the tariff indexation path.
- **Quarters.** The year is split using the quarterly pattern of the last fully actual year ({fc['profile_year']}):
  {', '.join(f'{s:.1%}' for s in fc['shares'])}.
- **Solar weather.** The solar part of each quarter is scaled by a solar weather index: GB solar load factor in the
  generation period against the average of the comparison years for the same days (PV_Live). Days already generated use
  actual solar, so the forecast tightens as the season passes. Days still to come use the case’s historic factor.
- **Calibration.** Actual ÷ model (at the weather each quarter was generated in) over the last four published
  quarters, applied to every forecast quarter. A quarter that comes in high lifts the rest of the year and later years;
  one that comes in low pulls them down.
- **Actuals.** Published quarters use Ofgem’s figure. A published annual levelisation replaces the sum of the quarters.
""")
        st.markdown("**Calibration quarters**")
        st.dataframe(fc["calibration_table"].style.format({"Actual £m": "{:,.1f}", "Model at that weather £m": "{:,.1f}",
                                                           "Solar weather index": "{:.3f}"}), width="stretch", hide_index=True)
        st.markdown("**Solar weather factors by calendar quarter** (weather index used for days still to come)")
        wf = fc["weather_factors"].rename(columns={k: v[3:] for k, v in fit.QUARTER_NAMES.items()})
        st.dataframe(wf.style.format("{:.3f}"), width="stretch")
        st.markdown("**Quarter detail**")
        st.dataframe(fc["quarters"][["Delivery year", "Quarter", "Case", "Status", "Cost £m", "Average-weather model £m",
                                     "Generation period", "Weather index", "Actual solar days"]]
                     .style.format({"Cost £m": "{:,.1f}", "Average-weather model £m": "{:,.1f}", "Weather index": "{:.3f}"}),
                     width="stretch", hide_index=True)
