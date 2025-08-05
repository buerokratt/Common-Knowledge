-- liquibase formatted sql
-- changeset ahmer-mt:20250730125816 ignore:true

INSERT INTO agency (
    base_id,
    name,
    type,
    external_id
) VALUES (
    '00000000-0000-0000-0000-000000000000',
    'ARVA',
    'api'::agency_type,
    '00000000-0000-0000-0000-000000000000'
);

INSERT INTO source (
    base_id,
    agency_base_id,
    url,
    type,
    status,
    next_scrapping_at
) VALUES (
    '00000000-0000-0000-0000-000000000000',
    '00000000-0000-0000-0000-000000000000',
    'ARVA',
    'api'::source_type,
    'new'::source_status_type,
    NOW()
);