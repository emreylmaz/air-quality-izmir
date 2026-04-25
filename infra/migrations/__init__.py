"""PostgreSQL migration runner package.

`infra.migrations.run` is the entrypoint invoked by `make migrate`. The
`*.sql` files in this directory are applied in lexicographic order and
tracked in the `schema_migrations` table for idempotency.

H4 sprint kararı (B2): saf psycopg, Alembic yok. Detaylar:
docs/sprints/sprint-04.md "Blocker'lar" bölümü.
"""
