---
name: shipit
description: Publish signed-off DCSS Bot changes through a checked pull request to main.
---

# DCSS Bot shipping workflow

## Repository and branches

- Repository: `Fuzzwah/dcss-bot`; Python WebTiles adapter, MiBe policy, supervisor,
  recording, evaluation, and pinned DCSS infrastructure.
- Integration target: `main`. Publish from a descriptive topic branch, never
  directly from `main`. Inspect `git worktree list` before branch operations.
- A previously merged topic is not a new release branch. Fetch origin and create
  a fresh topic from `origin/main` when the worktree can be preserved safely.
  Never reset, stash, force-push, discard changes, or delete an occupied branch.
- No `@committer` routing is required; perform git/GitHub operations directly.

## Scope and verification

1. Read `AGENTS.md`, inspect status, remote, complete changed/untracked path list,
   diff, and existing PR. Include only the user's signed-off changes.
2. Keep `.env`, credentials, `state/`, `runs/`, `.runtime/`, caches, recordings,
   and archived unfinished policy experiments out of the release.
3. Required behavioral validation: `uv run --locked pytest -q`. For recording or
   capture changes use `uv run --locked --extra video pytest -q`.
4. Behavioral releases require a real smoke against pinned DCSS0.34.1, using a
   fresh disposable account and separate control socket. Existing observed smoke
   evidence on the exact code is sufficient; do not replay valued characters.
   The standard opening scenario is:
   `env BOT_CHARACTER=mibe BOT_AGENT=rule BOT_SEED=8 uv run --locked --extra video python scripts/smoke.py --record --turns 130`.
   A focused real smoke may additionally exercise the changed protocol path.
   Record actual outcomes and artifact paths in `docs/verification.md`.
5. Docs-only releases need relevant CLI/config/link checks, not new gameplay.
   No standalone formatter/linter is configured. Do not manufacture hosted CI
   evidence or claim survival improvement from mechanism checks.

## Commit, PR and merge gates

- Immediately before commit/push, verify `git branch --show-current` is the
  intended topic. Stage exact approved paths; inspect the staged diff summary
  and check it for whitespace errors and private/runtime files.
- Commit with an imperative subject and push with an explicit upstream.
- Create a PR targeting `main`, with sections **Summary**, **Verification**,
  **Limitations**, and **Deployment**. Explain gameplay evidence and uncertainty;
  list actual test counts and real smoke results, not planned checks.
- Inspect the rendered PR body after creation. Inspect head/base, checks,
  review decision, draft status, mergeability and merge state. Wait for required
  checks/reviews; stop on failing, unavailable, blocked or pending required gates.
  If no hosted checks/reviews exist, report that explicitly, not as CI passing.
- No automatic post-PR branch rewrite/synchronization is required. If GitHub
  requires an update, use a non-destructive normal merge only after inspecting it,
  rerun affected validation and await refreshed gates. Never bypass protections.
- Normal merge method: **squash**, matching the existing main history. Match the
  inspected head commit when merging. Do not delete the occupied topic branch.
- Confirm `MERGED`, capture the PR URL and squash commit, and report final local
  worktree state. A local checkout may remain on the published topic branch.

## Deployment

Publication does not deploy or authorize restarting a game/server. Leave the
existing supervisor, saved character, recording, pause state, loopback server and
approved private LAN forward untouched. Runtime source changes require a separate
explicit deployment decision; do not resume a safety pause as part of shipping.
The current spectator URL is `http://10.1.1.232:8080/#watch-MiBeBot`.
