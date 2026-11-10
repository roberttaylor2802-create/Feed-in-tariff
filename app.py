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

import demand
import elexon
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
tab_solar, tab_demand, tab_fit = st.tabs(["Solar Generation Forecasting", "Demand", "FiT Cost Forecasting"])


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


# ================================================================ Demand tab

with tab_demand:
    st.subheader("GB metered demand, exemptions and FiT liable demand")
    base_d = demand.load_inputs()
    c = st.columns(4)
    d_method = c[0].selectbox("Gross demand forecast method", demand.METHODS, key="d_method",
                              help="Fitted to the last five delivery years of gross metered supply.")
    takeup = c[1].radio("BICS take-up", [100, 80, 60], horizontal=True, format_func=lambda v: f"{v}%", key="d_bics")
    carry = c[2].toggle("Carry this year’s variance forward", value=True, key="d_carry",
                        help="If published quarters this year run above or below the forecast, scale the rest of this "
                             "year and later years by the same ratio. The rest of this year is always scaled.")
    ca_source = c[3].radio("Class A exempt supply", ["Elexon S0621", "exemptions.csv"], key="d_ca",
                           help="Elexon publishes licence-exempt supply volumes daily since P442 (February 2025).")

    ca_q = None
    with st.expander("Class A licence-exempt supply from Elexon (S0621)", expanded=ca_source == "Elexon S0621"):
        st.write("Elexon’s Total Exempt Supply Volume (S0621) reports, for each settlement date, the electricity "
                 "supplied under licence exemptions behind licensed suppliers’ meters, as notified by Exempt Supply "
                 "Notification Agents under BSC Modification P442. Download it here, then upload the saved "
                 "`class_a_daily.csv` to GitHub to keep it.")
        cc = st.columns([1, 1, 2])
        ca_growth = cc[0].number_input("Growth after the last full quarter (% a year)", value=0.0, step=5.0, key="d_cag") / 100
        if cc[1].button("Update from Elexon", help="Downloads every settlement date since 27 February 2025 not yet saved, "
                                                  "and refreshes the last 120 days for later settlement runs."):
            bar = st.progress(0.0, text="Downloading from Elexon…")
            try:
                n = elexon.update(progress=lambda p: bar.progress(p, text="Downloading from Elexon…"))
                st.success(f"Saved {n} days.")
            except (requests.RequestException, ValueError) as e:
                st.error(f"Couldn’t download from Elexon: {e}")
        ca_daily = elexon.load_daily()
        if ca_daily.empty:
            st.info("No Elexon data saved yet. Click “Update from Elexon”; until then Class A uses `exemptions.csv`.")
        else:
            ca_q = elexon.quarterly(ca_daily)
            cc[2].download_button("Download class_a_daily.csv", ca_daily.assign(date=ca_daily["date"].dt.date)
                                  .to_csv(index=False).encode(), "class_a_daily.csv", "text/csv")
            show = ca_q.assign(**{"Delivery year": ca_q["year_start"].map(solar.label),
                                  "Quarter": ca_q["quarter"].map(demand.QUARTER_NAMES),
                                  "Exempt supply GWh": ca_q["class_a_twh"] * 1000})
            st.dataframe(show[["Delivery year", "Quarter", "Exempt supply GWh", "days", "complete"]]
                         .style.format({"Exempt supply GWh": "{:,.1f}"}), width="stretch", hide_index=True)
            st.caption(f"{len(ca_daily)} settlement dates, {ca_daily['date'].min():%d %b %Y} to "
                       f"{ca_daily['date'].max():%d %b %Y}. Settlement runs: {', '.join(sorted(set(','.join(ca_daily['run'].astype(str)).split(','))))}.")
    if ca_source != "Elexon S0621":
        ca_q = None

    with st.expander("Update with Ofgem supply volumes and exemption assumptions"):
        st.write("Each Ofgem FIT quarterly report gives the electricity suppliers reported supplying in the quarter and "
                 "how much was exempt for Energy Intensive Industries (and, from April 2027, BICS). Add each quarter "
                 "when Ofgem publishes it (status “actual”); the same report gives that quarter’s levelisation fund for "
                 "the FiT Cost tab. Edits last for this session: download the CSV and upload it to GitHub to keep them.")
        dq = st.data_editor(base_d["quarterly"], num_rows="dynamic", width="stretch", key="dq",
                            column_config={"status": st.column_config.SelectboxColumn(options=["actual", "estimate", "awaiting"]),
                                           "supplied_twh": st.column_config.NumberColumn(format="%.3f"),
                                           "eii_exempt_twh": st.column_config.NumberColumn(format="%.3f"),
                                           "bics_exempt_twh": st.column_config.NumberColumn(format="%.3f")})
        st.download_button("Download demand_quarterly.csv", dq.to_csv(index=False).encode(), "demand_quarterly.csv", "text/csv")
        st.markdown("**Exemption forecasts** (`exemptions.csv`). Blank EII and BICS years grow with gross demand.")
        dx = st.data_editor(base_d["exemptions"], width="stretch", key="dx", disabled=["scheme_year", "year_start"])
        st.download_button("Download exemptions.csv", dx.to_csv(index=False).encode(), "exemptions.csv", "text/csv")
        st.markdown("**Earlier years** (`demand_annual_history.csv`)")
        st.dataframe(base_d["annual"], width="stretch", hide_index=True)

    def _num(d, cols):
        d = d.copy()
        for col in cols:
            d[col] = pd.to_numeric(d[col], errors="coerce")
        return d

    dinp = {"quarterly": _num(dq, ["year_start", "quarter", "supplied_twh", "eii_exempt_twh", "bics_exempt_twh"]).dropna(subset=["year_start", "quarter"]),
            "annual": base_d["annual"],
            "exemptions": _num(dx, ["year_start", "eii_twh", "bics_full_takeup_twh", "class_a_twh", "other_twh"])}
    dinp["quarterly"][["year_start", "quarter"]] = dinp["quarterly"][["year_start", "quarter"]].astype(int)
    dfc = demand.forecast(dinp, d_method, takeup / 100, carry, class_a_q=ca_q, class_a_growth=ca_growth)
    LIABLE = {**demand.historic_liable(dinp), **demand.liable_by_year(dfc)}
    LIABLE_Q = demand.liable_by_quarter(dfc)
    DEMAND_LABEL = f"{d_method}, BICS {takeup}%"

    an = dfc["annual"].set_index("Delivery year")
    first = an.iloc[0]
    k = st.columns(4)
    k[0].metric(f"{an.index[0]} gross metered demand", f"{first['Gross TWh']:,.1f} TWh",
                help=f"{int(first['Quarters actual'])} published quarter(s) + forecast.")
    k[1].metric(f"{an.index[0]} liable demand", f"{first['Liable TWh']:,.1f} TWh")
    k[2].metric("Published quarters against forecast", f"{(dfc['variance'] - 1) * 100:+.1f}%",
                help="Actual supply in this year’s published quarters ÷ the forecast for those quarters.")
    k[3].metric(f"{an.index[1]} liable demand", f"{an.iloc[1]['Liable TWh']:,.1f} TWh",
                delta=f"{(an.iloc[1]['Liable TWh'] / first['Liable TWh'] - 1) * 100:+.1f}% as BICS starts", delta_color="off")

    # Annual gross: history and each method
    hist = dfc["history"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist["Delivery year"], y=hist["gross_twh"], mode="lines+markers", name="Outturn",
                             line=dict(color=TEAL, width=2.5)))
    last_x, last_y = hist["Delivery year"].iloc[-1], hist["gross_twh"].iloc[-1]
    palette = [BLUE, "#9674F7", "#F57D07", "#1F52F4", ASH]
    for i, m in enumerate(demand.METHODS):
        sel = m == d_method
        fig.add_trace(go.Scatter(x=[last_x] + list(dfc["methods"].index), y=[last_y] + list(dfc["methods"][m]),
                                 mode="lines", name=m, opacity=1 if sel else 0.35,
                                 line=dict(color=palette[i], width=2.5 if sel else 1.2, dash="dash")))
    fig.update_layout(title=dict(text="Gross metered demand by delivery year (TWh), before this year’s variance", font=dict(size=16)),
                      height=360, margin=dict(l=10, r=10, t=50, b=10), font=FONT, plot_bgcolor="white",
                      yaxis=dict(gridcolor="#EEF0F1", title="TWh"), legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, width="stretch")

    def qtable(col):
        q = dfc["quarters"]
        w = q.pivot(index="Delivery year", columns="Quarter", values=col)[list(demand.QUARTER_NAMES.values())]
        s = q.pivot(index="Delivery year", columns="Quarter", values="Status")[list(demand.QUARTER_NAMES.values())]
        out = w.copy().astype(object)
        for cq in w.columns:
            out[cq] = [f"{v:,.2f}" + (" (A)" if st_ == "Actual" else "") for v, st_ in zip(w[cq], s[cq])]
        out["Year"] = [f"{v:,.1f}" for v in w.sum(axis=1)]
        return out

    st.markdown("**Gross metered demand by quarter, TWh** — (A) = Ofgem reported")
    st.dataframe(qtable("Gross TWh"), width="stretch")

    st.markdown("**Exemptions by delivery year, TWh**")
    ex = an[["Gross TWh", "EII TWh", "BICS TWh", "Class A TWh", "Other TWh"]].copy()
    ex["Total exempt TWh"] = ex[["EII TWh", "BICS TWh", "Class A TWh", "Other TWh"]].sum(axis=1)
    ex["Liable TWh"] = an["Liable TWh"]
    ex["Exempt share"] = ex["Total exempt TWh"] / ex["Gross TWh"]
    st.dataframe(ex.style.format({**{col: "{:,.1f}" for col in ex.columns if "TWh" in col}, "Exempt share": "{:.1%}"}), width="stretch")

    fig2 = go.Figure()
    for col, colr in (("Liable TWh", TEAL), ("EII TWh", BLUE), ("BICS TWh", "#9674F7"), ("Class A TWh", "#F57D07"), ("Other TWh", ASH)):
        if an[col].abs().sum() > 0:
            fig2.add_trace(go.Bar(x=an.index, y=an[col], name=col.replace(" TWh", ""), marker_color=colr))
    fig2.update_layout(barmode="stack", title=dict(text="From gross metered demand to liable demand (TWh)", font=dict(size=16)),
                       height=360, margin=dict(l=10, r=10, t=50, b=10), font=FONT, plot_bgcolor="white",
                       yaxis=dict(gridcolor="#EEF0F1"), legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig2, width="stretch")

    st.markdown("**Liable demand by quarter, TWh** — the FiT Cost tab divides costs by these")
    st.dataframe(qtable("Liable TWh"), width="stretch")

    st.markdown("**Liable demand by forecast method, TWh** (at the selected BICS take-up and variance setting)")
    comp = {}
    for m in demand.METHODS:
        comp[m] = demand.forecast(dinp, m, takeup / 100, carry, class_a_q=ca_q,
                                  class_a_growth=ca_growth)["annual"].set_index("Delivery year")["Liable TWh"]
    st.dataframe(pd.DataFrame(comp).style.format("{:,.1f}"), width="stretch")

    with st.expander("Exemptions explained and how demand updates"):
        st.markdown("""
- **Gross metered demand** is the electricity licensed suppliers report supplying to customers in each quarter, from
  Ofgem’s FIT quarterly reports: the same metered base used for levelisation. It updates each quarter with the report
  that also gives the levelisation fund. Earlier years use DESNZ’s RO calculation, scaled to the same basis.
- **EII (Energy Intensive Industries):** exempt volumes are reported each quarter by Ofgem. Forecasts use DBT/BIST’s
  13.6 TWh for 2027/28, then grow with gross demand.
- **BICS (British Industrial Competitiveness Scheme):** exempt from April 2027. BIST forecast 15.7 TWh for 2027/28 at
  full take-up; the take-up setting scales it. Eligible businesses are confirmed in early 2027.
- **Class A exempt supply:** electricity supplied under licence exemptions (such as Class A) behind licensed
  suppliers’ meters. Since P442 (27 February 2025) Elexon publishes the daily total as S0621, used here when
  downloaded: complete quarters as reported, a part quarter scaled up, later quarters at the last four quarters’
  run-rate plus the growth you set. Elexon’s volumes are settlement volumes, adjusted for line losses, so they run
  slightly above customer-metered volumes. Check with your levelisation team whether suppliers’ reported FiT supply
  already excludes these volumes; if it does, deducting them again would double-count, so choose `exemptions.csv`
  (default 0).
- **Other:** any further exempt volume you want to model, such as new policy exemptions.
- **Forecast methods:** fitted to five years of annual gross demand, then spread across quarters using the average
  quarterly pattern of the full Ofgem years. Published quarters replace the forecast; the remaining quarters of the
  current year are scaled by how far published quarters ran above or below forecast, and so are later years when
  “carry this year’s variance forward” is on.
- **A faster signal:** LCCC publishes daily metered eligible demand, net of EII, through an open API. It could give an
  in-quarter estimate before Ofgem reports; it is not wired in yet.
""")
        st.markdown("**Annual history used**")
        st.dataframe(dfc["history"][["Delivery year", "gross_twh", "eii_twh", "basis"]].rename(
            columns={"gross_twh": "Gross TWh", "eii_twh": "EII exempt TWh", "basis": "Basis"}).style.format(
            {"Gross TWh": "{:,.1f}", "EII exempt TWh": "{:,.2f}"}), width="stretch", hide_index=True)


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
    c = st.columns(5)
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
    adj = c[4].number_input("Annual levelisation (£/MWh)", value=0.50, step=0.05, format="%.2f", key="f_adj",
                            help="Extra cost the annual levelisation adds on top of the four periodic quarters, per MWh "
                                 "of liable demand. Applied to every year until Ofgem publishes that year’s annual figure.")

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
    fc = fit.forecast(inp, weather, LIABLE, LIABLE_Q, lag, calibrate, adj)

    ann = fit.annual_cases(fc)
    cur = ann.iloc[0]
    k = st.columns(4)
    k[0].metric(f"{ann.index[0]} outturn estimate ({case.lower()})", f"£{fc['annual'].query('Case == @case')['Cost £m'].iloc[0]:,.0f}m",
                help=cur["Basis"])
    k[1].metric(f"{ann.index[0]} range", f"£{cur['Low']:,.0f}m to £{cur['High']:,.0f}m")
    k[2].metric("Calibration factor", f"{fc['calibration']:.3f}" if calibrate else "Off",
                help="Actual ÷ weather-adjusted model over the last four published quarters.")
    six = fc["annual"].query("Case == @case")["Cost £m"].sum()
    k[3].metric(f"Six-year total ({case.lower()})", f"£{six:,.0f}m",
                help="Includes the annual levelisation adjustment for each year.")
    cur_case = fc["annual"].query("Case == @case").iloc[0]
    st.caption(f"{ann.index[0]}: periodic quarters £{cur_case['Periodic quarters £m']:,.0f}m + annual levelisation "
               f"£{cur_case['Annual levelisation £m']:,.0f}m = £{cur_case['Cost £m']:,.0f}m, "
               f"or £{cur_case['Levy £/MWh']:.2f}/MWh on {cur_case['Liable demand TWh']:,.1f} TWh liable demand "
               f"(Demand tab: {DEMAND_LABEL}).")

    st.plotly_chart(quarter_chart(fc, case), width="stretch")
    st.caption("Periodic quarterly levelisation only. Dark bars are Ofgem’s published figures; light bars are forecasts. "
               "Whiskers show the low to high weather range. The annual levelisation is added to each year’s total below.")

    wide, status = fit.wide_table(fc, case)
    shown = wide.copy()
    for col in fit.QUARTER_NAMES.values():
        shown[col] = [f"£{v:,.1f}m" + (" (A)" if s == "Actual" else "") for v, s in zip(wide[col], status[col])]
    for col in ("Periodic total", "Annual levelisation", "Annual total"):
        shown[col] = [f"£{v:,.1f}m" for v in wide[col]]
    shown["Levy £/MWh"] = [f"£{v:,.2f}" for v in wide["Levy £/MWh"]]
    st.markdown(f"**{case} case, £m.** (A) = Ofgem actual.")
    st.dataframe(shown, width="stretch")

    st.markdown("**Annual totals by weather case, £m** (including annual levelisation)")
    st.dataframe(ann.style.format({c: "£{:,.0f}m" for c in fit.CASES}), width="stretch")

    st.markdown(f"**Levy per MWh by quarter, {case.lower()} case** (periodic levelisation ÷ liable demand from the Demand tab)")
    qq = fc["quarters"][fc["quarters"]["Case"] == case]
    lv = qq.pivot(index="Delivery year", columns="Quarter", values="Levy £/MWh")[list(fit.QUARTER_NAMES.values())]
    lv["Year incl. annual levelisation"] = fit.wide_table(fc, case)[0]["Levy £/MWh"]
    st.dataframe(lv.style.format("£{:.2f}"), width="stretch")

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
- **Actuals.** Published quarters use Ofgem’s figure.
- **Annual levelisation.** After each year Ofgem’s annual levelisation adds to the four periodic quarters. Each year’s
  total includes £{adj:.2f}/MWh × that year’s liable demand for this, until Ofgem publishes the year’s annual figure,
  which then replaces it. Liable demand comes from the Demand tab.
""")
        st.markdown("**Annual levelisation in past years** (from `annual_levelisation.csv` and four actual quarters)")
        if fc["annual_history"].empty:
            st.write("No published annual levelisation entered yet. Add past years’ annual figures to "
                     "`annual_levelisation.csv` to see the £/MWh adjustment they implied and check the setting above.")
        else:
            st.dataframe(fc["annual_history"].style.format({"Periodic quarters £m": "{:,.1f}", "Annual levelisation £m": "{:,.1f}",
                                                             "Adjustment £m": "{:,.1f}", "Adjustment £/MWh": "{:.2f}"}),
                         width="stretch", hide_index=True)
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
