-- liquibase formatted sql
-- changeset ahmer-mt:20250730125816 ignore:true

DELETE FROM source 
WHERE base_id = '00000000-0000-0000-0000-000000000000';

DELETE FROM agency 
WHERE base_id = '00000000-0000-0000-0000-000000000000';
