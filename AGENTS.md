# Working on DCSS Bot

## Goal and current scope

Build a locally runnable, observable autonomous Dungeon Crawl Stone Soup agent.
Survival and understandable decisions matter more than speed or kill count. A human
must be able to watch the real game through native WebTiles without controlling the
bot's authenticated session. Default play requires no paid API keys.

The working baseline is the deterministic `rule-v3` Minotaur Berserker of Trog.
`mibe` is currently the only implemented character profile. Other profiles and
engines are extension points, not already-supported gameplay. No LLM backend,
spellcasting policy, learning system or exact deterministic replay is bundled.
Keep improving MiBe without baking its choices back into shared infrastructure.

Read [README.md](README.md) for operation, [architecture](docs/architecture.md) for
protocol/research decisions, and [verification](docs/verification.md) for dated
results and known limitations. Verification documents are history, not proof that
an old process is still running or its gitignored artifacts still exist.

## Environment and implementation conventions

- Python 3.11+, `asyncio`, `uv`, pinned dependencies in `pyproject.toml` / `uv.lock`.
  Follow the existing straightforward modules; avoid extra services/frameworks.
- Linux host, Docker Compose or rootless Podman; Mac Firefox spectators use an
  SSH tunnel, optionally over Tailscale. Write shell examples compatible with Fish
  (`env NAME=value command`, not Bash-only assignment/export syntax).
- The game is unmodified DCSS **0.34.1**, commit
  `1eebc1a2892e1c89776a0d7a10691f8dac8d9796`. Do not silently track trunk/latest.
  Any deliberate version change must reconcile the Python version constants,
  container/bootstrap configuration, protocol fixtures and documentation.
- Use the public WebTiles protocol. Do not patch the engine or import an external
  adapter just to bypass an interface problem. Confirm licensing before copying
  external code; the research document explains why existing clients were not reused.

## Component ownership

- `src/dcss_bot/models.py`: public observations, validated actions, async `Agent` contract.
- `state.py`: sparse WebTiles updates, inventory/menu state and visibility filtering.
- `adapter.py`: authentication, verified RC setup, UI transactions, action dispatch,
  reconnect and character identity checks. No combat/equipment strategy here.
- `characters/base.py`: `CharacterProfile` setup and identity hooks.
  `characters/mibe.py`: MiBe `PROFILE`, `RuleBasedAgent` and equipment preferences.
  `characters/__init__.py`: explicit built-in registry / `module:PROFILE` loader.
- `agents.py`: `make_agent(identifier, character)`; `rule` calls the selected profile's
  factory. `module:Class` engines retain a no-argument constructor and `async decide`.
- `danger.py`: shared public-state risk annotations. Character tactics stay in profiles' policies.
- `supervisor.py`: single-flight execution, pacing, controls, bounded failures and saving.
- `recording.py`: JSONL, metrics and pause-aware anonymous-spectator capture.
- `evaluation.py`: isolated fresh-account seeded trials, provenance and comparisons.
- `config.py` / `cli.py`: configuration, credential storage and public commands.
- `infrastructure/`, `Dockerfile.server`, `compose.yaml`, `scripts/server.py`:
  pinned game/server deployment. Compose runs the game server; the Python bot runs separately.

A new combo should export a real `CharacterProfile` and policy. Use `--character
package.module:PROFILE`, or add a short registry alias. `--character` and `--agent`
are independent. Override profile methods for unusual setup/identity; don't add
per-combo branches to the adapter. New mechanics can require new shared validated
actions. Do not advertise another combo just because its creation menu works.

## Invariants and known traps

1. **Player-visible information only.** Remembered terrain is allowed; remembered
   monsters must not be presented as currently visible. Do not inspect saves,
   hidden maps or unidentified numeric inventory properties for strategy decisions.
2. **Sparse state is not a snapshot.** Preserve unchanged cell/player fields and
   handle moving/reidentified monsters. Protocol IDs alone are not tactical progress.
   Keep passive flora distinct from active hostile plants.
3. **One action in flight.** Native `input_mode == 0` is busy/rendering, not a command
   boundary. Wait for readiness, not merely a quiet packet gap. After reconnect,
   observe actual state; never replay an ambiguously accepted key.
4. **Fail safely at UI boundaries.** Unknown prompts pause or safely cancel;
   unsolicited confirmations are declined. Attribute prompts embed hotkeys such as
   `(S)trength`; the selected profile chooses the stat. Do not answer stale text.
5. **Creation is profile-owned.** Replace only profile-owned RC settings, preserve
   unrelated options, and verify exact `get_rc` readback after `set_rc`. The latter
   has no acknowledgement. Reconnect must not rewrite RC or recreate a finished run.
6. **Identity must be observed.** Player packets expose species/current god, not
   starting background. Fresh welcomes identify the combo. Native saved-game links
   use `[Name, a level N Species Background of God]`, not `[MiBe, D:1]`.
   `[playing]` carries no background proof. Build fixtures from actual protocol
   evidence or pinned upstream code, not plausible invented formats.
7. **Equipment state needs confirmation.** Use visible names and the native inventory
   menu's worn markers. Menu hotkeys and inventory letters can overlap; sending a
   wear command does not prove it succeeded.
8. **Preserve survival safeguards.** Stairs with visible hostiles are rejected;
   destinations and following enemies are not assumed safe. Keep regression coverage
   for merging slimes, low HP, unsafe stairs, ranged luring, ineffective pursuit,
   liquid fire and reach attacks. Do not cure a stall by disabling its safety bound.
   Cooldown-only `-Berserk` is not a reason to rest indefinitely.
9. **Pause is an action boundary.** Pause acknowledgements and single-step completion
   must reflect real completion, including recording transitions. User, safety and
   error pauses all stop capture. Normal pacing and recovery turns still record.
   Initially paused runs create no video until active; never-active runs have none.
   FFmpeg joins finalized active segments on orderly shutdown; preserve recoverable
   segments on failure. Video errors must not stop otherwise viable gameplay.
10. **Comparable runs need evidence.** Seeded trials require fresh accounts, verified
    RC plus native seed/character/version evidence, and separate strategy state.
    Keep profile identity/source provenance and reject mixed-combo comparisons.
    Turn-limit censoring, death, pauses and errors are distinct outcomes. Matching
    public traces are not proof of full engine determinism or better survival.

## Development and verification

Run from the repository root. Basic setup and suite:

```fish
uv sync --locked
uv run --locked pytest -q
```

For capture work, use the README's FFmpeg/Playwright setup and run the suite with
`uv run --locked --extra video pytest -q`. No separate formatter/linter command is
currently configured. Update both dependency declarations and lockfile if changing
packages; do not introduce an alternative dependency workflow.

For behavioral changes, add focused deterministic regressions and run the affected
suite plus the full suite before handover. Protocol tests use real loopback
WebSockets, but those do not replace a real DCSS smoke. Keep fixtures/tests free of
credentials and runtime-state dependencies. Test behavior and safety transitions,
not exact explanation wording or copies of implementation constants.

With the documented local server and video prerequisites ready, the known opening
acceptance scenario is:

```fish
env BOT_CHARACTER=mibe BOT_AGENT=rule BOT_SEED=8 uv run --locked --extra video python scripts/smoke.py --record --turns 130
```

This creates a fresh `Smoke...` account and isolated `.runtime/smoke-...sock`, checks
pause/resume/step, logs and native video playback, and saves the game. An unexpected
policy pause is a failed smoke, not permission to suppress the guard. For strategy
changes, also evaluate additional held-out seeds; do not optimize only for seed 8.
Record commands, actual outcomes, artifact paths and any unverified boundaries in
`docs/verification.md`. Update README when user-visible behavior changes.

Docs-only edits need CLI/configuration/link checks, not another game or full suite.
Do not change a historical test count or claim new live verification without running
it. Existing runtime artifacts may have been pruned; don't recreate claimed evidence.

## Protect running games and private state

- Do not use `MiBeBot` or another valued save as a disposable test. Use a fresh
  alphanumeric username (3–20 characters) and a separate control socket. Check
  existing services before starting another server against `state/` or port 8080.
- `--control-socket` is a global option **before** `run`, `status`, `stop`, etc.
  Inspect `status`/the recorded `reason` before resuming a safety pause. Stop/save
  normally before infrastructure restarts; don't steal a live control socket/lock.
- Credentials are generated beside the control socket, not unconditionally in
  `state/`. Preserve that directory when resuming. Smoke credentials live in
  `.runtime/`; evaluation credentials normally live in `state/eval-<id>/`.
  `.runtime/` therefore contains more than disposable build caches.
- A seeded start requires a new account, even if an existing account has no save.
  To resume, clear `BOT_SEED` (including any `.env` default), select `dcss-seeded`,
  and reuse the account's credentials. See the README's explicit resume command.
- Keep `.env`, credentials, `state/`, `runs/` and `.runtime/` out of commits. Do not
  print secrets or automatically delete saved games, large logs or recordings.
- Bind the published WebTiles port to loopback. Spectators use their own anonymous
  browser connection, never the bot's login. Do not expose controls/public ports or
  enable Tailscale Funnel as a convenience.
- A paused connection is not permanent storage: upstream's five-hour idle limit
  still applies. Save/stop for long breaks. `summary.json` records the save outcome;
  a disconnect after an ambiguous command is not proof of a successful save.

## Handover and publication

Report what changed, verification actually exercised and remaining limitations.
Keep unverified Docker Compose/native/Mac networking paths distinct from observed
Podman/Linux-browser results. Don't claim a bot is running based on old handover notes.

Commit, push, PR and merge only when requested. The repository is
`Fuzzwah/dcss-bot`, with `main` as the integration branch; inspect the current
branch/worktree and current remote checks/review requirements at release time.
Do not force-push, discard unrelated changes or delete a branch used by a worktree.
Local test evidence is not a hosted CI result. Source publication does not deploy
or authorize restarting the user's server.
