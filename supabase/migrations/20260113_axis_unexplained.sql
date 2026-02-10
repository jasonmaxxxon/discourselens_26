-- "Dark Matter" Storage: Comments that don't fit current axes but have high engagement.
-- Used for V8 Discovery (Open-set).

CREATE TABLE IF NOT EXISTS axis_unexplained (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    text TEXT NOT NULL,

    -- Signals
    max_axis_score FLOAT NOT NULL,
    top_axis_name TEXT,
    score_margin FLOAT,
    engagement_score INT NOT NULL,

    -- Context Traceability
    post_id TEXT,
    comment_id TEXT,
    run_id TEXT,

    -- Workflow
    status TEXT DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLUSTERED', 'DISMISSED')),
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Dedup: Allow same phrase in different posts, but not duplicates within same post
    UNIQUE(text, post_id)
);

CREATE INDEX IF NOT EXISTS idx_unexplained_engagement ON axis_unexplained(engagement_score DESC);
