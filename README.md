# DCSS Bot

An autonomous DCSS bot with modular species/background policies. The first and
currently only built-in profile is **MiBe: Minotaur Berserker of Trog**, controlled
through the official WebTiles protocol and watched in the native graphical browser
client. No paid API keys. DCSS **0.34.1** is pinned to commit
`1eebc1a2892e1c89776a0d7a10691f8dac8d9796`; the engine is not modified.

## Start and watch

Run these from the repository root on the Linux host. Commands work in Fish.
Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), and Docker with Compose
or rootless Podman. The first server build compiles the game and downloads assets.

**1. Start the server** (Docker Compose):

```fish
mkdir -p state; and docker compose up --build -d webtiles
```

Creating `state` as your user first keeps bot credentials/control files writable
when Docker writes its own server files as root.

For Podman, or Docker without Compose, use this foreground command instead:

```fish
python3 scripts/server.py
```

Do not start both against the same `state/` directory. The server binds only to
`127.0.0.1:8080`. Native-build instructions and dependency details are in
[infrastructure/README.md](infrastructure/README.md).

**2. Start the bot** in another terminal:

```fish
uv run --locked dcss-bot run
```

It registers the local `MiBeBot` account automatically, generates a private password,
creates the character, and plays. An existing saved character resumes rather than
being deleted. Use `--username AnotherBot` for a different account/save slot.

**3. Watch** at **http://127.0.0.1:8080/**. Select **MiBeBot** in the games list,
or open **http://127.0.0.1:8080/#watch-MiBeBot**. **Do not log in as the bot.**
Spectating is anonymous, on a separate connection; it cannot move the character.

### Firefox on your MacBook

Keep the server private. From a Mac terminal, open an SSH tunnel to the Linux
machine, using its Tailscale name/IP if desired:

```fish
ssh -N -L 8080:127.0.0.1:8080 fuz@YOUR-LINUX-TAILSCALE-NAME
```

Then open **http://localhost:8080/#watch-MiBeBot** in Firefox. Leave the tunnel
terminal open. The browser and WebSocket both use the forwarded port. If Mac port
8080 is occupied, use `-L 8081:127.0.0.1:8080` and browse to `localhost:8081`.
No router port forwarding, public bind, Tailscale Funnel, or bot credentials needed.

## Controls

In a second Linux terminal (or over SSH):

```fish
uv run --locked dcss-bot status
uv run --locked dcss-bot pause
uv run --locked dcss-bot step
uv run --locked dcss-bot resume
uv run --locked dcss-bot delay 1
uv run --locked dcss-bot delay 0
uv run --locked dcss-bot stop
```

`pause` preserves the live game connection. `step` executes one decision/action and
stays paused. A decision can include a menu response or an inventory-selection
transaction; it does not always advance a game turn. `delay 0` accelerates play,
not turn batching. A pause cannot undo an already accepted attack or native armour
change; it stops further commands at the next action boundary.

`stop` saves/disconnects; it does not abandon or kill the character. Ctrl-C also
requests graceful stop. Start paused with `uv run --locked dcss-bot run --paused`.
Controls use a private local Unix socket at `state/control.sock`, not an exposed
HTTP API. Only one supervisor may own that socket. For separate simultaneous
experiments, specify separate usernames and `--control-socket` paths.

Stop infrastructure **after** stopping the bot:

```fish
docker compose down
```

For the bootstrap's Podman container: `podman stop dcss-webtiles`.
Persistent `state/` is not deleted by these commands.

## Logs and playable recordings

Every invocation creates `runs/<UTC-timestamp>-<id>/` containing:

* `metadata.json`: run ID, engine revision, character and agent configuration.
* `actions.jsonl`: decisions, explanations, timing and outcomes.
* `observations.jsonl`: player-visible observations.
* `events.jsonl`: lifecycle changes, errors and significant events.
* `status.json`: latest readable live state.
* `summary.json`: final result and metrics, including resumed-run turn delta.

Inspect a run:

```fish
uv run --locked dcss-bot inspect runs/RUN-DIRECTORY
```

For optional native spectator **WebM video**:

```fish
sudo apt-get install ffmpeg
uv sync --locked --extra video
uv run playwright install chromium
uv run --locked --extra video dcss-bot run --record
```

Some hosts need Chromium system dependencies (`uv run playwright install-deps chromium`
uses the system package manager and may require sudo). Recording runs a second,
anonymous, headless browser. **Pausing the bot also pauses capture**, including
safety/strategy pauses. Resume restarts capture; single-step records its action
then pauses again. Startup with `--paused` records nothing until play or a step.
Normal action pacing and in-game recovery turns are still recorded.
This does not disable WebTiles' five-hour game-idle limit. For overnight breaks,
save/stop the bot and restart it later.

Close/stop the bot normally to join the active segments into one `gameplay.webm`,
without the paused gaps or re-encoding. FFmpeg must be available on the bot host.
A run that never leaves pause produces no video. `spectator.png` captures the
rendered game; `events.jsonl` retains recording transitions and original timestamps.
Temporary `.video-*` segments are removed after successful finalization, retained
for recovery if capture/join fails. Video errors are logged and disable recording
without stopping gameplay.

Firefox and VLC can play WebM directly. Copy the file to your Mac with `scp`,
or serve recordings only on localhost and forward that port:

```fish
python3 -m http.server 8090 --bind 127.0.0.1 --directory runs
```

Optional MP4 conversion:

```fish
ffmpeg -i runs/RUN-DIRECTORY/gameplay.webm -c:v libx264 -pix_fmt yuv420p runs/RUN-DIRECTORY/gameplay.mp4
```

Video is immediate visual replay. JSONL preserves decisions and observations for
analysis, **not exact deterministic engine replay**. Native saves, morgues and
server logs are under `state/`; see the infrastructure document for paths.

## Seeded survival evaluation

Keep the normal watchable bot running or paused. Evaluation uses **new accounts**
and a **separate private control socket**, never the `MiBeBot` save:

Use the updated server image: seeded runs use native game mode `dcss-seeded`,
not the normal `dcss` slot label. For an existing installation, stop bots first,
stop the container, then start `python3 scripts/server.py --rebuild` (or
`docker compose up --build -d webtiles`). Saves remain under `state/`.

```fish
uv run --locked dcss-bot evaluate --seeds 1 2 3 --repeats 2 --max-turns 700
```

Runs are sequential. Each gets the same character setup, explicit
`pregen_dungeon = incremental`, turn/action limits and pacing. The adapter writes
`game_seed` through WebTiles and verifies the rc readback. The evaluator also
requires the native startup banner to report the requested seed and the selected
species/background, plus turn zero, the profile's religion, and engine release
0.34.1. Seed 0 means random in DCSS and is rejected. Fresh accounts avoid
accidentally comparing resumed games. Select the combo with `--character mibe`
or `BOT_CHARACTER`; one batch evaluates one character profile.

`EVAL_RUN` prints the account, run directory and control socket. Watch that account
in the native lobby, or use `#watch-ACCOUNT` with the same SSH tunnel. Add
`--delay 0.4` for comfortable spectating and `--record` with the `video` extra for
WebM recording. `--seconds-per-run 600` bounds each trial. A safety pause saves and
ends that trial, then evaluation continues; it does not wait for unattended input.
Ctrl-C saves the current character and cancels the remaining batch. Sending `stop`
to the printed evaluation socket also cancels the remaining batch; default
`dcss-bot stop` still addresses the normal bot.

Results live in `runs/evaluations/<timestamp-id>/`:

* `manifest.json`: seeds, limits, engine pin, selected character module and its
  source hash, recursive package source hashes and socket.
* `results.jsonl`: one result per trial, including character identity, failure
  reasons and seed proof.
* `report.json`: per-agent metrics, paired starting-condition checks and repeat
  hashes of initial public state and action/state traces.
* `runs/<run-id>/`: normal complete recordings, `console.log`, `evaluation.json`
  and the exact verified `game.rc`.

Credentials remain private under `state/eval-<id>/`, not in recordings. Saved
evaluation characters are retained; no automatic deletion or account reuse.
Full map observations can consume substantial disk space in long batches.

Compare installed strategies on identical seeds:

```fish
uv run --locked dcss-bot evaluate --seeds 1 2 3 --agents rule your_package:YourAgent --max-turns 1000
uv run --locked dcss-bot inspect runs/evaluations/BATCH-DIRECTORY
```

Deaths, safety pauses, errors, timeouts and turn-limit censoring are distinct.
Observed turns are **not** estimated lifespan or a win rate: an inactive bot can
survive longer without progressing. Read depth, kills, failure reasons and traces
together. Matching hashes are empirical repeatability of public traces, not a
general deterministic replay guarantee. Different agent choices can produce
different combat outcomes. The command exits nonzero for pauses, setup errors,
timeouts, interrupted batches or incomparable starting conditions, while retaining
all results.

Comparisons reject mixed character profiles. Historical reports without character
identity retain descriptive metrics but cannot establish matched starting combos.

For the longer opening-dungeon progression suite:

```fish
uv run --locked dcss-bot evaluate --seeds 1 2 3 4 5 6 7 8 --max-turns 2500 --max-actions 6000 --seconds-per-run 1000
```

The additional `exploration` evidence records first arrival/descent turns by
location, observed walkable terrain, distinct visited positions, movement revisits,
and action/turn costs by objective. A no-reveal exploration turn is not necessarily
wasted: reaching another frontier can require backtracking. These metrics never
claim full-map coverage or infer intermediate positions in multi-turn actions.
Aggregate arrival times include their sample count; floors not reached are
reported separately by outcome rather than assigned a zero arrival time.
The eight-seed rule-v2/rule-v3 comparison, per-seed outcomes, regression evidence
and playable recording are documented in [verification](docs/verification.md#verified-repaired-policy-comparison).
It demonstrates faster progression, **not** a general survival improvement.

For a single seeded watchable run, use a **new** username:

```fish
uv run --locked dcss-bot --control-socket state/seed-demo.sock run --username SeedDemo --seed 12345 --paused
```

Resume its saved character later with the same account/socket **without `--seed`**,
selecting the seeded game mode:

```fish
env DCSS_GAME_ID=dcss-seeded uv run --locked dcss-bot --control-socket state/seed-demo.sock run --username SeedDemo --paused
```

Providing `--seed` to an existing account is deliberately rejected without
modifying its rc or playing its save. Custom servers can override
`DCSS_SEEDED_GAME_ID`; normal play uses `DCSS_GAME_ID`.

## Configuration and strategy

Defaults require no `.env`. To override:

```fish
cp .env.example .env
vi .env
```

Environment variables override `.env`, and CLI flags override both. Useful flags:
`--delay 0.4`, `--max-turns 10000`, `--max-actions 10000`, `--record`, `--paused`.
The generated account password lives in `state/credentials-USERNAME.json` with
mode `0600`, never in run metadata. `.env`, state, recordings and caches are ignored.
Keep backups private: the server state contains accounts and saved characters.

### Character modules

Character selection and decision-engine selection are independent:

```fish
uv run --locked dcss-bot run --character mibe --agent rule
```

This is also the default without either flag. Set `BOT_CHARACTER=mibe` in `.env`
for persistent selection. `--agent rule` creates the selected character module's
default policy; `--agent module:Class` overrides the decision engine without changing
character creation. Both `run` and `evaluate` accept `--character`.

[`characters/mibe.py`](src/dcss_bot/characters/mibe.py) owns the current `PROFILE`
and `RuleBasedAgent`: species/background, starting religion/weapon, preferred
level-up stat, autopickup preferences, equipment scoring and combat/exploration
tactics. Shared transport, controls and recording do not choose a combo.

To support another combination:

1. Add a module with a real `async decide(observation) -> Action` policy and export
   a `CharacterProfile` instance named `PROFILE`. Use `characters/mibe.py` as the
   existing complete example, not a universal policy for other species.
2. Define its identifier, display abbreviation, species, background, starting god
   (`""` for none), weapon (`None` if no weapon choice), preferred stat, policy
   factory and character-owned `rc_options`.
3. For unusual creation/advancement or changing public identity, subclass
   `CharacterProfile` and override its setup/identity methods in that module.
   Keep combat and equipment decisions in its policy.
4. Load it with `--character your_package.your_combo:PROFILE`, or register a short
   name in `BUILTIN_CHARACTERS` in `characters/__init__.py`. Installed modules are
   trusted Python code, not data-only configuration.
5. Add combo-specific behavioral fixtures and verify a fresh live game before
   advertising support. Use a separate `--username`; selecting a profile never
   converts or deletes an existing saved character.

The adapter applies the profile's creation/RC choices and checks public identity.
It rejects a conflicting saved combo when the native lobby identifies it, and
checks species/religion on attachment. A fresh welcome also verifies the starting
background. Unknown UI still pauses safely.
Profile-owned RC options override native rc-editor values; unrelated settings are
preserved. Changing a weapon/stat preference does not retrofit an existing save.

No second combo or new tactics are included in this refactor. Modules using the
existing action vocabulary need no adapter/supervisor changes. New mechanics such
as spellcasting should extend shared actions, not add per-combo transport branches.

### Current MiBe policy

The `rule-v3` policy explores toward known walkable boundaries adjoining unseen
terrain, rather than walking onto every already-revealed room tile. It opens
reachable ordinary doors, retires unproductive viewpoints until local knowledge
changes, and descends once no useful reachable frontier remains. Temporary cloud
blockage causes bounded waiting, not premature descent or movement through clouds.
Paths never step into unknown cells. Exploration is subordinate to combat, healing and danger:
the agent attacks adjacent enemies, recovers between fights, considers visible
threat ratings/statuses/damage, and retreats onto previously visited ground when
that reduces immediate danger.
It uses identified healing/escape consumables and Trog's berserk for appropriate
committed fights. It does not treat stairs as an automatic escape: visible hostiles
block stair commands. Merging or already enlarged slime creatures trigger explicit
danger handling. Unknown UI states pause or are safely cancelled; run/stall limits
prevent unbounded command loops.

The policy recognises common shooters (including dart slugs), prefers nearby known
opaque-wall cover, briefly lures enemies around it, and cautiously closes on lone
ordinary shooters rather than waiting for free ranged attacks. Cover is a heuristic,
not an exact implementation of Crawl's line-of-sight rules.
Luring a lone ordinary shooter is bounded: after twelve encounter turns, a healthy
agent can cautiously close rather than circle cover forever. Brief loss of sight
or a changed protocol monster ID does not restart that timer.

An adjacent enemy following a retreat is evidence that distance-only movement did
not provide escape. Further retreat must improve cover or the enemy formation;
otherwise the agent considers a timely available berserk or a single defensive
attack, while preserving healing, cooldown and slime safeguards. This reduces one
observed pursuit failure mode; it does not make disengagement universally safe.

Bombardier beetles are recognised as ranged threats. Liquid fire requires movement
on clear visible terrain, even while berserk or after the enemy dies; it is not a
reason to rest. Visible “hits you from afar” messages trigger a reach-weapon
response without using hidden monster equipment. The agent can close a safe step
instead of accepting free attacks or wasting an already-active berserk.

Equipment decisions use visible names/enchantments and actual worn markers read
from the native inventory menu. The agent collects/selects useful axe and body-armour
upgrades, verifies wear outcomes, avoids repeated failed requests, and rejects
unrankable artefacts or hazardous brands rather than guessing their properties.
Post-berserk recovery can advance bounded hidden status timers without tripping
the short action-cycle detector; the `-Berserk` cooldown alone does not cause rest.

This is an opening-dungeon baseline, not a winning bot. It lacks sophisticated
ranged tactics, comprehensive equipment evaluation, spellcasting, branch planning,
and long-term learning. Threat heuristics cannot guarantee survival or that an
unseen destination behind stairs is safe. Never rely on it for an existing valued
character. Reusing an account resumes that save; it does not create a comparable
seeded benchmark run automatically.

## Extend and test

Implement `async decide(observation) -> Action`, using the immutable public models
in `src/dcss_bot/models.py`. Run an installed strategy with
`--agent your_package.your_module:YourAgent`. Strategy code cannot send WebSocket
messages through this interface; actions are validated and dispatched by the
adapter. No provider credentials or SDK are required by the core.
An LLM backend is not bundled; endpoint/model/key configuration belongs in a
strategy plugin. Provider exceptions and invalid actions cause a logged pause.
The provider-neutral extension contract is described in the architecture document.
The MiBe implementation is now imported from `dcss_bot.characters.mibe`; shared
danger annotations are in `dcss_bot.danger`. The former strategy/helper exports
from `dcss_bot.agents` were removed; that module only loads decision engines.

```fish
uv sync --locked
uv run --locked pytest -q
```

Tests cover protocol deltas/visibility, real loopback WebSocket connection and
reconnection, UI handling, action validation, survival regressions, lifecycle
controls and recorder metrics. A live upstream server is required for the full
integration smoke; mocked protocol tests are not its replacement.

With the server running, reproduce the real-game acceptance check:

```fish
uv run --locked --extra video python scripts/smoke.py --record --turns 130
```

This creates a disposable account, verifies character creation and
pause/resume/step, plays real turns, checks the logs, and opens the finalized WebM
in Chromium to prove it decodes. It fails explicitly if the agent unexpectedly
pauses. `--username ACCOUNT` can resume a prior smoke save; turn limits are
relative to that invocation, not the character's lifetime.

The current implementation passed **333 automated tests**. Seeded runs have checked
matching initial states and identical 300-turn public action/state traces across
fresh accounts, live native spectating, armour acquisition and playable video.
The earlier infrastructure milestone also exercised Firefox, container restart
persistence and real character death. Exact artifacts, comparison results and
unverified deployment paths are listed in [verification](docs/verification.md).

See [architecture and research](docs/architecture.md) for component boundaries,
licensing, integration tradeoffs and the visibility contract.
