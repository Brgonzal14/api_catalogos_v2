#!/bin/bash
set -e

echo "🔧 Ensuring extensions/indexes..."
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE parts ADD COLUMN IF NOT EXISTS pn_search TEXT;

UPDATE parts
SET pn_search = upper(regexp_replace(part_number_full, '[^A-Za-z0-9]+', '', 'g'))
WHERE pn_search IS NULL;

CREATE INDEX IF NOT EXISTS ix_parts_part_number_full ON parts (part_number_full);
CREATE INDEX IF NOT EXISTS ix_parts_pn_full_pattern ON parts (part_number_full text_pattern_ops);
CREATE INDEX IF NOT EXISTS ix_parts_description_trgm ON parts USING gin (description gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_parts_pn_search ON parts (pn_search);
CREATE INDEX IF NOT EXISTS ix_parts_pn_search_pattern ON parts (pn_search text_pattern_ops);
CREATE INDEX IF NOT EXISTS ix_parts_pn_search_trgm ON parts USING gin (pn_search gin_trgm_ops);
SQL

echo "✅ DB ready."