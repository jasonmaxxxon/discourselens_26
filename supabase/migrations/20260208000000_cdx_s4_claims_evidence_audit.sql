-- Sprint 4: Claim ↔ Evidence binding (audit artifacts)
create table if not exists public.threads_claims (
    id uuid primary key default gen_random_uuid(),
    post_id bigint not null references public.threads_posts(id) on delete cascade,
    cluster_key int null,
    run_id text not null,
    claim_type text not null,
    scope text not null,
    text text not null,
    source_agent text not null default 'analyst',
    confidence double precision null,
    tags jsonb null,
    prompt_hash text null,
    model_name text null,
    created_at timestamptz not null default now()
);

create index if not exists threads_claims_post_run_idx
    on public.threads_claims (post_id, run_id);

create index if not exists threads_claims_post_cluster_idx
    on public.threads_claims (post_id, cluster_key);

create table if not exists public.threads_claim_evidence (
    id bigserial primary key,
    claim_id uuid not null references public.threads_claims(id) on delete cascade,
    evidence_type text not null,
    evidence_id text not null,
    span_text text null,
    created_at timestamptz not null default now()
);

create index if not exists threads_claim_evidence_claim_idx
    on public.threads_claim_evidence (claim_id);

create index if not exists threads_claim_evidence_lookup_idx
    on public.threads_claim_evidence (evidence_type, evidence_id);

create table if not exists public.threads_claim_audits (
    id bigserial primary key,
    post_id bigint not null,
    run_id text not null,
    build_id text null,
    verdict text not null,
    dropped_claims_count int not null default 0,
    reasons jsonb null,
    created_at timestamptz not null default now()
);
