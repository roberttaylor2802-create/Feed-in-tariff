# FiT forecasting dashboard

Streamlit app with three tabs:

1. **Solar Generation Forecasting** – GB solar for the current scheme year against the previous
   5 or 10 years, with low / central / high weather scenarios.
2. **Demand** – GB gross metered demand by quarter and year, exemptions (EII, BICS, Class A,
   other) and FiT liable demand, with five forecasting methods.
3. **FiT Cost Forecasting** – levelisation cost for each quarter and each delivery year
   (April to March) for the next six years, updated by Ofgem's published levelisation, with
   low / central / high solar weather cases.

## Files (all at the top level of the repository)

| File | What it is | Update when |
|---|---|---|
| `app.py` | The Streamlit app | – |
| `solar.py` | Solar data and calculations | – |
| `fit.py` | FiT cost forecast | – |
| `demand.py` | Demand and exemptions forecast | – |
| `elexon.py` | Downloads Class A licence-exempt supply (Elexon S0621) | – |
| `solar_daily.csv` | Daily GB solar (PV_Live) | Use the app's update button, then upload the file |
| `quarterly_actuals.csv` | Ofgem levelisation fund by quarter | Each quarter Ofgem publishes |
| `annual_levelisation.csv` | Ofgem annual levelisation by year | Each annual reconciliation |
| `tariff_assumptions.csv` | Tariff uplift each April (CPI) | When CPI forecasts change |
| `demand_quarterly.csv` | Ofgem-reported supply and EII exempt volume by quarter | Each quarter Ofgem publishes |
| `exemptions.csv` | EII, BICS, Class A and other exemption forecasts | When forecasts change |
| `class_a_daily.csv` | Daily licence-exempt supply from Elexon (created by the app) | After clicking “Update from Elexon”, download and upload it |
| `demand_annual_history.csv` | Annual gross supply before 2024/25 (DESNZ RO basis) | Rarely |
| `fit_portfolio.csv` | Every FiT installation costed by delivery year | New Ofgem installation report |
| `requirements.txt` | Python packages | – |

## Adding a new Ofgem quarter

Each Ofgem FIT quarterly report gives three numbers: the levelisation fund, the electricity
supplied and the EII exempt volume.

1. **Demand tab → Update with Ofgem supply volumes:** enter supplied TWh and EII exempt TWh on the
   quarter's row, set status to `actual`, download `demand_quarterly.csv`.
2. In the app, open **FiT Cost Forecasting → Update with Ofgem levelisation and tariff assumptions**.
2. Enter the levelisation fund in £m on the right quarter's row and set its status to `actual`.
   The forecast updates immediately.
3. Click **Download quarterly_actuals.csv** and upload it to this repository (Add file → Upload
   files) so the change is kept.

Or edit `quarterly_actuals.csv` directly on GitHub (pencil icon) and commit.

## Run

On Streamlit Community Cloud, deploy this repository with `app.py` as the main file.
Locally (Python 3.10+): `pip install -r requirements.txt` then `streamlit run app.py`.

## How the cost forecast works

- **Bottom-up model:** every installation on Ofgem's FiT register costed at its 2026/27 tariff,
  with generation at average weather from DESNZ FiT load factors, until its eligibility ends.
  Uplifted each April by `tariff_assumptions.csv`.
- **Quarters:** split using the last fully actual year's quarterly pattern.
- **Solar weather:** the solar share of each quarter is scaled by a weather index from PV_Live.
  Levelisation mostly pays for the previous quarter's generation (setting in the app), so actual
  solar already measured feeds the next quarter's forecast. Days still to come use the 10th /
  50th / 90th percentile of past weather for that quarter (low / central / high).
- **Calibration:** actual ÷ weather-adjusted model over the last four published quarters,
  applied to every forecast quarter, so over- or under-runs carry through to the rest of the year
  and later years.
- **Actuals:** published quarters replace the forecast.
- **Annual levelisation:** each year's total adds an annual levelisation adjustment (default
  £0.50/MWh × liable demand) on top of the four periodic quarters. Once Ofgem publishes a year's
  annual levelisation, that figure replaces the adjustment. Entering past years' annual figures shows
  the £/MWh they implied.
