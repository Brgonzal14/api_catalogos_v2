CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Índices base (si la tabla ya existe al momento del init)
CREATE INDEX IF NOT EXISTS ix_parts_part_number_full ON parts (part_number_full);
CREATE INDEX IF NOT EXISTS ix_parts_part_number_root ON parts (part_number_root);
CREATE INDEX IF NOT EXISTS ix_parts_supplier_id ON parts (supplier_id);
CREATE INDEX IF NOT EXISTS ix_parts_catalog_id ON parts (catalog_id);

-- prefijo rápido: LIKE 'ABC%'
CREATE INDEX IF NOT EXISTS ix_parts_pn_full_pattern
ON parts (part_number_full text_pattern_ops);

-- texto rápido: ILIKE '%xxx%'
CREATE INDEX IF NOT EXISTS ix_parts_description_trgm
ON parts USING gin (description gin_trgm_ops);