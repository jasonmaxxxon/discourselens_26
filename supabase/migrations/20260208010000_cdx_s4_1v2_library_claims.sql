-- Sprint 4.1 v2: Library-first claims + EvidenceRef denorm
alter table if exists public.threads_claims
    add column if not exists claim_key text not null default '',
    add column if not exists status text not null default 'audited',
    add column if not exists cluster_keys jsonb,
    add column if not exists primary_cluster_key int,
    add column if not exists audit_reason text,
    add column if not exists missing_evidence_type text,
    add column if not exists confidence_cap double precision;

create unique index if not exists threads_claims_claim_key_uq
    on public.threads_claims (claim_key);

create index if not exists threads_claims_post_status_idx
    on public.threads_claims (post_id, status);

create index if not exists threads_claims_post_primary_cluster_idx
    on public.threads_claims (post_id, primary_cluster_key);

alter table if exists public.threads_claim_evidence
    add column if not exists source text not null default 'threads',
    add column if not exists locator_type text not null default 'comment_id',
    add column if not exists locator_value text not null default '',
    add column if not exists locator_key text not null default '',
    add column if not exists cluster_key int,
    add column if not exists author_handle text,
    add column if not exists like_count int,
    add column if not exists capture_hash text,
    add column if not exists evidence_ref jsonb;

create index if not exists threads_claim_evidence_locator_idx
    on public.threads_claim_evidence (locator_key);

create index if not exists threads_claim_evidence_cluster_idx
    on public.threads_claim_evidence (cluster_key);

alter table if exists public.threads_claim_audits
    add column if not exists kept_claims_count int not null default 0,
    add column if not exists total_claims_count int not null default 0;
