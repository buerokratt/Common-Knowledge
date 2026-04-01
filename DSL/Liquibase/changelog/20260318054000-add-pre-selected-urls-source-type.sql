-- liquibase formatted sql
-- changeset ruwinirathnamalala:20260318054000 ignore:true
-- Add dedicated source_type enum value for pre-decided URL list sources

ALTER TYPE source_type ADD VALUE IF NOT EXISTS 'pre_selected_urls';
