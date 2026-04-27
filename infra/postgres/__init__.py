"""PostgreSQL operational scripts (seeds, maintenance helpers).

This package hosts data-side companions to the migration runner under
`infra.migrations`:

* `seed_dim_station` — UPSERT the Izmir station catalog (`config/stations.yaml`)
  into `dim_station`. Idempotent; safe to run after every `make migrate`.

The directory also keeps `init.sql` (role bootstrap) which is consumed by
the local `docker-compose` Postgres entrypoint, not by Python code.
"""
