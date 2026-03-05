# Timeline/Investigate/Compare Contract Gap Report

Last updated: 2026-02-22  
Spec reference: `/Users/tung/Desktop/DLens_26/docs/cdx-week1/05_TIMELINE_EVOLVE_V1_SPEC.md`

## 1) Gap Scan Summary

Status:
- Backend endpoint coverage: mostly sufficient for Week-1 deterministic spec.
- Frontend contract coverage: missing wrappers/types for comments endpoints and a few required fields.

Decision:
- No new backend endpoint added.
- Minimal frontend type/api contract updates applied.

## 2) Gaps and Minimal Fixes

| Gap | Where found | Minimal change | Risk | Verification |
|---|---|---|---|---|
| Missing frontend wrapper for `/api/comments/by-post/{post_id}` | `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts` | Added `api.getCommentsByPost(postId, {limit,offset,sort})` | Low | Typecheck/build |
| Missing frontend wrapper for `/api/comments/search` | `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts` | Added `api.searchComments({q,author_handle,post_id,limit})` | Low | Typecheck/build |
| Missing `CommentItem` and comments response types | `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/types.ts` | Added `CommentItem`, `CommentsByPostResponse`, `CommentsSearchResponse` | Low | Typecheck/build |
| `PostItem` missing compare-relevant fields from existing `/api/posts` payload | `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/types.ts` | Added optional `analysis_version`, `phenomenon_id` | Low | Existing pages compile unchanged |
| `EvidenceItem` missing `evidence_id` field used by spec requirements | `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/types.ts` | Added optional `evidence_id` | Low | Existing evidence flows compile unchanged |

## 3) Backend Contract Check (No change required)

Validated against current backend:
- `/api/comments/by-post/{post_id}` already returns: `post_id,total,items[id,text,author_handle,like_count,reply_count,created_at]`  
  Evidence: `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1521`
- `/api/comments/search` already returns comment list fields needed by spec  
  Evidence: `/Users/tung/Desktop/DLens_26/webapp/routers/api.py:1548`
- `/api/evidence`, `/api/claims`, `/api/clusters`, `/api/jobs/{id}/summary`, `/api/posts` already provide required deterministic fields for Week-1 scope.

## 4) Files Changed

- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/types.ts`
- `/Users/tung/Desktop/DLens_26/dlcs-ui/src/lib/api.ts`

No backend Python files changed in this prompt.

## 5) Build/Lint Evidence

Planned validation command:
- `npm --prefix /Users/tung/Desktop/DLens_26/dlcs-ui run build`

Result:
- Passed on 2026-02-22.
- Build artifacts emitted successfully; no TypeScript errors.
- Note: Vite reported large chunk warning for shader bundle (non-blocking for this scope).

## DOCUMENTATION UPDATE (CHANGE-AWARE)

This CDX introduces changes in the following areas:
- [ ] Endpoint behavior / responses
- [x] Data schema / payload shape
- [ ] Ownership or source-of-truth rules
- [ ] Dataflow (sync/async, enrichment, background jobs)
- [ ] Lifecycle / state transitions

For each checked item, the corresponding integration documents
MUST be updated:

- Endpoint changes -> `/Users/tung/Desktop/DLens_26/docs/ENDPOINTS.md`
- Schema/payload changes -> `/Users/tung/Desktop/DLens_26/docs/CONTRACTS.md`
- Flow or ownership changes -> `/Users/tung/Desktop/DLens_26/docs/FLOWS.md`
- Lifecycle or edge-case changes -> `/Users/tung/Desktop/DLens_26/docs/SYSTEM_OVERVIEW.md`

The CDX is NOT considered complete
until all impacted documents reflect the new reality.
