-- Migration: 003_add_biology.sql
-- Adds Biology to the subjects table for the Cameroon GCE A-Level tutor.
-- Run this in your Supabase SQL Editor.

INSERT INTO subjects (name, slug, prompt_template, retrieval_k) VALUES
('Biology', 'biology', NULL, 5)
ON CONFLICT (slug) DO NOTHING;
