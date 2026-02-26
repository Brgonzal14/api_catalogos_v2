#!/bin/bash
set -e

HOST="${PGHOST:-db}"
PORT="${PGPORT:-5432}"

echo "🔧 Waiting for Postgres at ${HOST}:${PORT}..."
for i in {1..60}; do
  if pg_isready -h "$HOST" -p "$PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "🔧 Ensuring extensions/indexes..."
psql -h "$HOST" -p "$PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$
BEGIN
  IF to_regclass('public.parts') IS NULL THEN
    RAISE NOTICE 'parts table does not exist yet, skipping indexes.';
    RETURN;
  END IF;

  ALTER TABLE parts ADD COLUMN IF NOT EXISTS pn_search TEXT;

  UPDATE parts
  SET pn_search = upper(regexp_replace(part_number_full, '[^A-Za-z0-9]+', '', 'g'))
  WHERE pn_search IS NULL;

  CREATE INDEX IF NOT EXISTS ix_parts_part_number_full ON parts (part_number_full);
  CREATE INDEX IF NOT EXISTS ix_parts_part_number_root ON parts (part_number_root);
  CREATE INDEX IF NOT EXISTS ix_parts_supplier_id ON parts (supplier_id);
  CREATE INDEX IF NOT EXISTS ix_parts_catalog_id ON parts (catalog_id);

  CREATE INDEX IF NOT EXISTS ix_parts_pn_full_pattern
  ON parts (part_number_full text_pattern_ops);

  CREATE INDEX IF NOT EXISTS ix_parts_description_trgm
  ON parts USING gin (description gin_trgm_ops);

  CREATE INDEX IF NOT EXISTS ix_parts_pn_search ON parts (pn_search);
  CREATE INDEX IF NOT EXISTS ix_parts_pn_search_pattern ON parts (pn_search text_pattern_ops);
  CREATE INDEX IF NOT EXISTS ix_parts_pn_search_trgm ON parts USING gin (pn_search gin_trgm_ops);
END $$;
SQL

echo "✅ DB ready."