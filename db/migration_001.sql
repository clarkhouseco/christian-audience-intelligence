-- Migration 001: add duration and short-video detection to youtube_videos
-- Run once in the Supabase SQL editor.

ALTER TABLE youtube_videos
  ADD COLUMN IF NOT EXISTS duration_seconds INT,
  ADD COLUMN IF NOT EXISTS is_short         BOOLEAN;
