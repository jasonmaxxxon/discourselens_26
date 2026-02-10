-- Repurpose analysis_reviews as a generic labeling channel (Axis deprecated).

ALTER TABLE analysis_reviews
  ADD COLUMN IF NOT EXISTS label_type TEXT,
  ADD COLUMN IF NOT EXISTS schema_version TEXT NOT NULL DEFAULT 'v1',
  ADD COLUMN IF NOT EXISTS decision JSONB,
  ADD COLUMN IF NOT EXISTS comment_id TEXT,
  ADD COLUMN IF NOT EXISTS cluster_key INT,
  ADD COLUMN IF NOT EXISTS notes TEXT;

ALTER TABLE analysis_reviews
  ALTER COLUMN decision TYPE JSONB
  USING to_jsonb(decision);

UPDATE analysis_reviews
SET label_type = COALESCE(label_type, 'other')
WHERE label_type IS NULL;

UPDATE analysis_reviews
SET decision = COALESCE(decision, '{}'::jsonb)
WHERE decision IS NULL;

UPDATE analysis_reviews
SET comment_id = COALESCE(comment_id, '')
WHERE comment_id IS NULL;

ALTER TABLE analysis_reviews
  ALTER COLUMN axis_id DROP NOT NULL,
  ALTER COLUMN axis_version DROP NOT NULL;

ALTER TABLE analysis_reviews
  ALTER COLUMN label_type SET NOT NULL,
  ALTER COLUMN decision SET NOT NULL,
  ALTER COLUMN comment_id SET NOT NULL;
