"""Machine learning module — AQI short-horizon forecasting.

Sprint 14 deliverable. Contains:

* `forecast` — Prophet-based 24-hour ahead AQI forecasting per station.

Production training is invoked from a CLI / Airflow task; Streamlit
panel (Sprint 13) reads the persisted predictions table.
"""
