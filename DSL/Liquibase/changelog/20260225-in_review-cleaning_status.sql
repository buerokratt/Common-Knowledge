-- Add 'in_review' to source_status_type and source_file_status_type enums
-- Add cleaning_status column to source table
-- Rollback included

-- 1. Add 'in_review' to source_status_type
ALTER TYPE source_status_type ADD VALUE IF NOT EXISTS 'in_review';


-- 2. Add 'in_review' to source_file_status_type
ALTER TYPE source_file_status_type ADD VALUE IF NOT EXISTS 'in_review';

-- Rollback
-- Remove cleaning_status column (cannot remove enum values in Postgres easily)
-- To rollback, drop the column only
-- (Manual intervention needed to remove enum values if required)
