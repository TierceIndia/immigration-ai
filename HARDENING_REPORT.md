# Hardening Report - immigration-knowledge (release/complete)

**Branch:** release/complete  
**HEAD:** 1609015  
**Date:** 2026-10-28  
**Working tree:** clean

## 1. Commit History

1. 1609015 fix: hard-code HS256 for JWT, validate sub and exp claims
2. 8a2d018 chore: replace LICENSE with All rights reserved notice, ask for license choice
3. a71b3aa chore: add .gitattributes with LF line endings
4. cc71c3b fix: revert allowlist default to .gov.au, keep env override
5. f81af53 feat: add backup/restore scripts and LICENSE placeholder
6. 595e2a9 feat: replace python-jose with PyJWT, keep HS256, update auth service
7. 0de0c77 fix: improve prod hardening - Caddy cert volume, proxy headers, IP-based throttling with email bypass, allowlist flexibility, security docs
8. b33cd28 P0-4: Container hardening - cap_drop ALL, read_only rootfs with tmpfs
9. ae96bbc chore: remove plan doc, duplicate gold files and local report artifacts
10. 2b916e0 docs: refresh all docs to current state (Playwright, search, CI, UI, env)

**Note:** My earlier report said "6 commits total" but there are 8 commits from b33cd28 onward. Commit b33cd28 contains far more than just P0-4 (JWT/SSRF/throttle/headers, 2 migrations, 30+ tests) - I did not rewrite history.

## 2. Migrations

### d3e4f5a6b7c8_add_fetch_retrieval_events.py
**Changes:**
- Adds 9 columns to `fetch_log`: `retrieval_id` (String(36), nullable), `acquisition` (String(32)), `final_url` (String(2048)), `content_type` (String(128)), `limiter_wait_ms` (Integer), `browser_launch_ms` (Integer), `goto_ms` (Integer), `selector_wait_ms` (Integer), `selector_timed_out` (Boolean)
- Creates unique index `uq_fetch_log_retrieval_id` on `retrieval_id`

**Rows touched:** 0 (schema only, additive)  
**Reversible:** Yes - downgrade drops all columns and index in reverse order  
**Impact:** Additive, no data migration needed

### e5f6a7b8c9d0_retire_needs_manual_capture.py
**Changes:**
- Updates all `government_sources` where `status='NEEDS_MANUAL_CAPTURE'` to `status='ACTIVE'`
- Removes `NEEDS_MANUAL_CAPTURE` from check constraint `ck_source_status`

**Rows touched:** Any existing sources with status NEEDS_MANUAL_CAPTURE (data update)  
**Reversible:** Constraint can be restored in downgrade, but status values cannot be reverted automatically  
**Impact:** Data update + constraint change
EOF
