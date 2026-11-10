# FiT forecasting: solar

GB solar generation for the current scheme year (1 April to 31 March) against the previous
5 or 10 years, plus low, central and high weather scenarios.

## Files

All files sit together at the top level of the repository:

- `app.py` – the Streamlit page.
- `solar.py` – data loading, PV_Live download and calculations.
- `solar_daily.csv` – daily GB solar generation (GWh) and installed capacity (MWp) from
  Sheffield Solar PV_Live, April 2016 to 9 November 2026 (2020/21 missing).
- `requirements.txt` – Python packages.

## Run it

On Streamlit Community Cloud, deploy this repository with `app.py` as the main file.

On your own computer (Python 3.10+):

```
pip install -r requirements.txt
streamlit run app.py
```

## Update the data

Click **Update from PV_Live** in the app's sidebar to add any days missing since the last date
in `solar_daily.csv`. On Streamlit Cloud that update is lost when the app restarts, so to keep it,
download the latest `solar_daily.csv` and upload it to this repository.

## Method

- **At today's capacity:** each day's load factor (GWh ÷ installed capacity × 24 h) × the latest
  installed capacity, so past years compare weather rather than the growth in panels.
- **Band:** min to max, or mean ± 2 standard deviations, of the comparison years on the same day.
  29 February is dropped so leap years line up.
- **Weather scenarios:** every comparison year's weather replayed at today's capacity. Current
  year = actual to date + each year's weather for the remaining days; next year = each year's
  full-year weather. Low / central / high = P10 / P50 / P90, min / mean / max, or mean ± 1 sd.
- **Weather factor:** scenario generation ÷ the comparison years' average for the same period;
  use it as the solar weather factor in the FiT cost model.
- PV_Live excludes solar sites in the Balancing Mechanism.
