-- Staging area for V7 Axis Candidates
-- This table captures "Novel" high-score comments for human review.

CREATE TABLE IF NOT EXISTS public.axis_candidates (
    id uuid primary key default gen_random_uuid(),

    text text not null,
    suggested_axis text not null,

    score double precision not null,
    containment_score double precision not null,
    novelty_reason text,

    post_id text,
    comment_id text,
    run_id text,

    status text not null default 'PENDING'
        check (status in ('PENDING','APPROVED','REJECTED')),
    created_at timestamptz not null default now(),
    reviewed_at timestamptz,

    unique(text, suggested_axis)
);

CREATE INDEX IF NOT EXISTS idx_axis_candidates_status
    ON public.axis_candidates(status);

CREATE INDEX IF NOT EXISTS idx_axis_candidates_created_at
    ON public.axis_candidates(created_at desc);
