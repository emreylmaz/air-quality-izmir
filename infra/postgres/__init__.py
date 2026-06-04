"""PostgreSQL operational scripts (seeds, maintenance helpers).

This package hosts data-side companions to the migration runner under
`infra.migrations`:

* `seed_dim_station` — UPSERT the Izmir station catalog
  (`config/stations.yaml`) into `dim_station`. Idempotent; safe to run
  after every `make migrate`.
* `seed_dim_time` — UPSERT hourly rows for the 2024-2025 window into
  `dim_time`, deriving `season` from the calendar month and `is_holiday`
  from `config/tr_holidays.yaml`. Idempotent; re-runs propagate holiday
  catalog edits via `ON CONFLICT (time_id) DO UPDATE`.

The directory also keeps `init.sql` (role bootstrap) which is consumed by
the local `docker-compose` Postgres entrypoint, not by Python code.
"""
