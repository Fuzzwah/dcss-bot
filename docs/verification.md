# Verification and handover — 2026-10-30

## Environment and tested infrastructure

Debian Linux, rootless Podman. Docker was not installed in the execution shell;
`Dockerfile.server` was built with Podman and the bootstrap was run against that
image. Official unmodified DCSS 0.34.1, commit
`1eebc1a2892e1c89776a0d7a10691f8dac8d9796`.

Observed listener: **127.0.0.1:8080**, not a public interface. The host's Tailscale
IPv4 address during verification was `100.108.49.90`; SSH listens on port 22.
From the MacBook, the current host can be reached with:

```fish
ssh -N -L 8080:127.0.0.1:8080 fuz@100.108.49.90
```

Then visit **http://localhost:8080/#watch-MiBeBot**. SSH authentication from the
MacBook was not exercised. The implementation was visually verified in Chromium
and Firefox 142.0.1 on Linux; Firefox reported no page errors.

## Commands and results

* Official image build and `python3 scripts/server.py`: successful.
* Live Python client: registration, Minotaur Berserker/Trog creation, six movement
  turns without manual input. No engine changes or hidden map access.
* Initial autonomous run: reached turn 182, killed a ball python and rat, remained
  at 19/19 HP, visible in the native anonymous spectator lobby.
* `uv run --locked pytest -q`: **76 passed in 2.34s**. Tests cover connection and
  reconnection over real loopback WebSockets, sparse state/visibility, action and
  prompt validation, control races, death, safety fixtures, credentials and recorder
  behavior. Slime/emergency/stair regressions are fixtures, not claims that those
  deep-dungeon encounters were reached in the live run.
* `uv run --locked --extra video python scripts/smoke.py --record --turns 130`:
  **130 game turns, 130 turn-advancing decisions, four kills**. Character creation,
  pause/resume, one-action stepping, delay changes, run limits, logs and video checked.
* Replayed the plant/identity regression save with `--username Smokec65b4e7c`:
  **130 additional game turns, 129 turn-advancing decisions, three kills**. The
  difference between turns and decisions comes from native action duration, not
  fabricated wait-loop counts.
* Independently loaded a temporary `module:Class` strategy: played three real turns,
  resumed its save, played three more, confirmed native saving, and terminated.
* Disposable death-test policy deliberately stood in combat: real character death
  at turn **169**, **170 decisions**, supervisor result/state `dead`, no new
  character created. The recorded last damage message was “The dart slug bites
  you.” It is labeled last-damage evidence, not an explicit engine death-cause field.
* Closed/reconnected a real player connection: turn 7 remained turn 7 (no action
  replay). Stopped/restarted the server container and resumed that saved character
  at turn 7; a subsequent action and reconnect retained turn 8.

## Inspectable artifacts (runtime data, intentionally gitignored)

Fresh recorded acceptance run:

`runs/20261030T062646.631397Z-90e6474efa05/`

* `smoke.json`: acceptance evidence.
* `gameplay.webm`: **23.44 seconds**, VP8, 1280×960, 25fps; decoded in Chromium and
  inspected as a video preview grid. No audio.
* `spectator.png`, metadata, actions, observations, events, final summary.

Resumed regression run:

`runs/20261030T063055.051063Z-dfa609f71d84/`

* Same acceptance checks; finalized **26.12-second** WebM decoded successfully.

Death verification:

`runs/20261030T063333.665116Z-403004c032a7/summary.json`

Paused handover session at creation time:

`runs/20261030T063544.017111Z-cf9209ca505b/`

`MiBeBot`, Dungeon:1, turn 182, HP 19/19, pacing 0.4 seconds. Resume with
`uv run --locked dcss-bot resume`; inspect current state with `dcss-bot status`.
The server and bot were left running. Its video finalizes when the bot stops.
Earlier failed-run directories are retained as diagnostic evidence.

## Defects found by real execution and fixed

* Generic `AGENT` environment variable collided with the host's `AGENT=1`;
  configuration now uses `BOT_AGENT`.
* Passive flora has hostile wire attitude; it no longer traps the policy in
  waiting/berserking. Trivial attacking enemies are still threats.
* An ID-only monster delta with a fresh client ID inherits unchanged cell fields,
  matching the native client, instead of becoming an invented “unknown” threat.
* Save confirmations are modal menu titles; prompt extraction no longer reuses a
  stale dungeon message. Native save confirmation was exercised successfully.
* The supervisor caught the original repeated-action stalls and paused them;
  it did not silently consume its entire action budget.

## Seeded evaluation milestone

`uv run --locked pytest -q`: **169 passed in 19.86s** after seed-selection,
fresh-account protection, reconnect protocol, equipment, cover tactics, hatch
navigation, recovery timer and aggregate-report regressions.

Initial seeded baseline batch:
`runs/evaluations/20261030T075512Z-976b4f2d/`.
Seeds 1/2/3 exposed recovery-loop detection, excluded hatch terrain and passive
demonic-plant classification. These are fixed; `-Berserk` cooldown alone no longer
causes rest. A separate pre-change dart-slug observation reproduced indefinite
waiting; the rule now recognises the shooter.

Paired comparison:
`runs/evaluations/20261030T080444Z-fa7d1f77/`.

```fish
env PYTHONPATH=.runtime uv run --locked dcss-bot evaluate --agents baseline_agent:RuleBasedAgent rule --seeds 1 2 3 --max-turns 1000 --seconds-per-run 400
```

The temporary baseline module was the old strategy on the corrected shared
infrastructure, **not the entire unmodified old system**. Its source is archived
as `baseline_agent.py` inside that batch. To repeat the comparison after cleanup,
point `PYTHONPATH` at the archived batch instead of `.runtime`.

| Seed | Baseline observed turns | rule-v2 observed turns | Baseline kills | rule-v2 kills |
|---|---:|---:|---:|---:|
| 1 | 1000 | 1000 | 15 | 17 |
| 2 | 1000 | 1000 | 14 | 14 |
| 3 | 1000 | 1000 | 14 | 14 |

All six runs reached the turn cap without death or safety pause; all paired
starting-state and rc hashes matched. All remained on Dungeon:1, reaching at most
XL2. This small, capped sample **does not establish superior survival**. Rule-v2
seed 1 turns 301–311 exercised cover/luring around a real dart slug and hobgoblin;
the corresponding action logs expose observations, reasons and outcomes.

Recorded repetition check:
`runs/evaluations/20261030T080444Z-9f916274/`.

```fish
uv run --locked --extra video dcss-bot evaluate --seeds 1 --repeats 2 --max-turns 300 --record --seconds-per-run 180
```

Both runs reached 300 turns, 288 turn-advancing decisions and seven kills, with
identical initial-state, rc and public action/state-trace hashes. Ring mail was
picked up at turn 30 and wearing was confirmed at turn 31 through the native
inventory UI. Native anonymous browser spectating was visually checked during
the second run. The first finalized `gameplay.webm` is **54.48 seconds**, 1280×960,
VP8, 25fps; Chromium decoded it with `readyState=4`, and its preview grid was
visually inspected. Video recording is optional.

Lifecycle checks against the real server:

* Five-second trial deadline: `20261030T080720Z-17d1d5e9`, 18 turns, classified
  `timeout`, clean save. The deadline reason is now explicitly included in results.
* Explicit stop cancelled the remaining trial, saved and removed socket/lock:
  `20261030T081025Z-12eb4062/control-smoke.json`.
* SIGINT did the same: `20261030T081043Z-8339ff71/control-smoke.json`.
* An existing seeded account rejected a new seed before play; its rc hash remained
  unchanged and the connection closed.
* The ordinary `MiBeBot` remained paused at turn 182, HP19/19 throughout the batches.

The first real seeded-reconnect check exposed missing lobby save advertisements:
the upstream `-save-json` helper did not inherit the configured game directory,
and custom-seed saves needed a matching seeded game-mode entry. The bootstrap now
sets `CRAWL_DIR` and exposes `dcss-seeded`; no engine code is modified.
The rebuilt image was installed, and two further fresh seeded-mode runs each
reached 130 turns with matching rc, initial-state and public-trace hashes:
`runs/evaluations/20261030T083350Z-29ec311d/`.
Live reconnect then exposed split initialization packets; reconnect now waits for
actual character state instead of returning an empty observation, and refuses any
creation dialogue. Fresh `SeedRecon620253272b`, seed 12345, reconnected at exactly
turn **1 → 1**, retaining the rc hash, and saved cleanly.

The final five-second timeout check on the upgraded seeded mode is
`runs/evaluations/20261030T083737Z-79d8f9f3/`: 10 turns and the explicit
`Wall-clock deadline reached (5s)` result. MiBeBot's saved game was restored after
the server upgrade at turn 182, HP19/19, with rule-v2 and no actions taken.
Final paused handover invocation:
`runs/20261030T083953.236201Z-c3d85ac7e5f1/`.
The upgraded server and latest rule-v2 supervisor were left running; native
spectating was visually checked at `http://127.0.0.1:8080/#watch-MiBeBot`.

## Longer progression milestone

`uv run --locked pytest -q`: **226 passed in 22.49s**. Added behavioral coverage
for frontier exploration, clouds blocking a frontier, observed pursuit, bounded
ranged luring across LOS/ID changes, liquid fire, reach weapons, native advancement
hotkeys, delayed command completion, and per-location evaluation metrics.

The predeclared development suite is seeds **1–8**, with **2,500 game turns**,
6,000 actions and a 1,000-second deadline per trial:
`runs/benchmarks/deeper-v3/plan.json`. Accounts and control sockets are isolated;
the ordinary MiBeBot never participates. Seed-isolated comparison jobs ran
concurrently, each evaluating its strategies sequentially. These seeds were used
to diagnose and improve the policy, so they are not a held-out performance set.
Unless prefixed with `runs/`, artifact paths in this section are relative to
`runs/benchmarks/deeper-v3/`.

Diagnostic artifacts are retained:

* `baseline/`: archived rule-v2 on the pre-fix adapter. Seven runs paused at the
  native level-up prompt; seed 8 died to a goblin's pursuit attacks. One earlier
  harness-interrupted 92-turn launch is excluded from comparisons.
* `pilot/`: frontier exploration exposed the same level-up prompt, a cloud-blocked
  starting corridor, and a dart-slug circle costing over 2,200 lure turns.
* `comparison/`: gracefully cancelled experiments after the lure defect was found;
  marked by `superseded.json`, not presented as completed comparisons.
* `final/` and `frozen-comparison.json`: a completed, source-frozen diagnostic
  comparison. It reached Dungeon:4 and exposed reach-weapon deaths, liquid-fire
  deaths and two level-transition readiness pauses. The directory name predates
  these findings; it is not the final repaired-policy verification.

The adapter now recognises decorated level-up hotkeys and sends uppercase `S`.
A live saved prompt was resumed at turn 829, XL3; single-step reached 830, resume
advanced to 1103, pause held that exact turn, and stop saved cleanly:
`resume/20261030T090244.939991Z-b6c662b1666f/` beneath the benchmark directory.
An earlier lowercase-key smoke failed explicitly before this correction.

After the readiness fix, the interrupted native stair transition resumed on
Dungeon:3 at turn 1373, stepped to 1374, then played another 99 turns and saved:
`recovery/20261030T094750.979732Z-75c96305c512/`. No stair command was replayed.
Loopback WebSocket tests separately delay completion beyond the old 120ms quiet
gap and verify command, prompt, menu, death and timeout boundaries.

The diagnostic seed-4 WebM is 626.96 seconds, 1280×960 VP8. It was decoded,
played and sought to 400 seconds in Chromium, showing actual Dungeon:2 gameplay:
`final/20261030T092102Z-0b8cca5b/runs/20261030T093245.555459Z-56c219c03abb/gameplay.webm`.

MiBeBot was saved and restarted with the updated policy without advancing a turn:
**turn 182, HP19/19, Dungeon:1, paused**, zero actions in the new invocation.
Current handover run: `runs/20261030T095151.248983Z-5177d011a975/`.
The server and upgraded supervisor were left persistent; native anonymous
spectating at `http://127.0.0.1:8080/#watch-MiBeBot` was visually checked.

### Verified repaired-policy comparison

`verified/report.json` and `verified/results.jsonl` combine all **16 completed
trials**, with eight comparable pairs, 16 valid fresh starts, DCSS0.34.1 reported
by every game, and identical package source hashes across the eight batch
manifests. The archived rule-v2 strategy and rule-v3 use the **same repaired
adapter/supervisor**; this is a policy comparison, not an unchanged-old-system
comparison. No source edits occurred during these trials.

| Seed | rule-v2 outcome / turn | v2 depth | rule-v3 outcome / turn | v3 depth | v3 XL |
| --- | --- | --- | --- | --- | --- |
| 1 | limit / 2500 | D2 | limit / 2500 | D4 | 5 |
| 2 | pause / 1017 | D1 | limit / 2500 | D4 | 6 |
| 3 | limit / 2500 | D2 | dead / 1611 | D3 | 3 |
| 4 | limit / 2500 | D2 | limit / 2500 | D3 | 6 |
| 5 | limit / 2500 | D2 | limit / 2500 | D4 | 6 |
| 6 | limit / 2500 | D2 | dead / 2417 | D4 | 4 |
| 7 | limit / 2500 | D2 | dead / 2218 | D4 | 6 |
| 8 | dead / 198 | D1 | limit / 2500 | D3 | 6 |

The v2 pause is a four-action cycle caught by the supervisor. No v3 trial paused,
timed out or failed at a protocol boundary. A turn-limit result is censored, not a
win or a lifespan estimate. There were **three v3 deaths versus one v2 death**:
better progression is demonstrated; a general survival improvement is not.

Median first arrival on D2 was **792 turns (8/8 reached)** versus **1712 (6/8)**.
Arrival includes involuntary transitions such as shafts; it is not a floor-clear
score. Aggregate exploration turns per newly observed walkable cell fell from
**1.238 to 0.440**. No-reveal exploration turns were **10,083/13,164** for v2 and
**5,853/11,065** for v3. Backtracking can be necessary, so “no reveal” is not
automatically wasted movement. There were no missing location/position/map/turn
observations in these metrics.

Live regression evidence:

* Seed 8 closed on the gnoll while berserk at turns 788–790, attacked it at
  790–792, and survived to the cap instead of the earlier gnoll death.
  `verified/seed8-deeper-evidence.json` also shows completed stairs returning
  input mode 1 with the new floor already observed.
* Seed 2 moved twice while on fire, turns 1797–1799; the fire cleared with 29 HP
  remaining. It continued to D4 and the cap.
  See `verified/seed2-deeper-evidence.json`.
* Seed 3 was banished by Sigmund's visibly described +4 scythe of distortion at
  turn 1490, then died to a jiangshi in Abyss:1. Seed 6 died to Eustachio.
  Seed 7 survived the Menkaure fight but died to an orc's trident while retreating
  from Eustachio, summons and orcs. Reach-weapon handling is not a guarantee of
  escape from a surrounding group.

Both seed-4 policies produced WebM recordings. The repaired-policy video was
decoded and **played/sought to 600 seconds in Chromium**, with readyState 4, no
media error and actual D3 gameplay visible. Duration: **702.64 seconds**;
1280×960 VP8:
`verified/20261030T095002Z-78404088/runs/20261030T100203.623449Z-58323f5af3b1/gameplay.webm`.
The temporary parallel-comparison launcher was removed after all jobs finished;
the evaluator itself and archived baseline/evidence remain available.


## Pause-aware recording

`uv run --locked --extra video pytest -q`: **257 passed in 26.97s**.
Regression coverage includes initial pause, single stepping, action-boundary
acknowledgements, automatic/error/reconnect pauses, transition failures,
final-frame handling, segment preservation and cancellation cleanup.

An isolated real Minotaur run started with `--record --paused`, remained paused
for five seconds, then stopped and saved in **0.33 seconds**. It took zero actions,
created no WebM or capture directory, and logged zero video errors:
`runs/20261030T231140.610587Z-b73dd2a3fa05/`.
This replaces the old paused-only capture path that could time out on shutdown.

The first unseeded recording smoke stepped, resumed, paused and resumed, then
hit the existing strategy cycle guard at turn 70. The smoke command reported that
unexpected policy pause rather than falsely claiming 130 turns. Capture stopped
automatically at the safety boundary and finalized a decoded **14.8-second WebM**,
omitting the three deliberate four-second pause holds:
`runs/20261030T231052.765214Z-821862cd7f2c/`.

The completed fixed-seed smoke used a fresh seed-8 Minotaur and exercised
**130 game turns, 129 meaningful actions, 132 decisions**, with pause/resume/step
verified. Repeat with a fresh automatically named account:

```fish
env BOT_SEED=8 uv run --locked --extra video python scripts/smoke.py --record --turns 130
```

Evidence: `runs/20261030T231544.342144Z-5a874bedc331/smoke.json`.

| Measurement | Seconds |
| --- | ---: |
| Observed post-ready wall time | 39.74 |
| Deliberate paused holds | 12.01 |
| Final joined video | 26.36 |

Native live spectating was visually checked. The joined `gameplay.webm` was
fully decoded with FFmpeg and played/sought to 20 seconds in Chromium
(readyState 4, no media error). Frame 20 visibly contains the first single step
at game time 1.0. Recording events show three resumed/paused intervals and no
video errors. The save was retained; no MiBeBot game was used for these checks.

Recording is optional but now requires system FFmpeg when capture first starts.
Only active intervals are concatenated; original JSONL timestamps are unchanged.
Brief spectator-loading frames can appear at segment boundaries. Pausing capture
does not disable the server's independent five-hour game-idle timeout.

## Character-module refactor

MiBe remains the only supported built-in combination. Its rule-v3 policy and
equipment preferences moved into `characters/mibe.py`; setup is selected through
`CharacterProfile`, independently of the decision engine. No tactical changes
were introduced. Alternate-profile tests exercise setup/stat/identity boundaries,
not claims of a second fully playable character.

Final regression command:

```fish
uv run --locked --extra video pytest -q
```

**333 passed in 41.72s**, including profile-specific menus and RC settings,
same-species/wrong-background rejection, native save descriptions, seeded
reconnection, comparison identity, and the existing survival/recording suite.

The default-profile real recorded smoke used:

```fish
env BOT_SEED=8 uv run --locked --extra video python scripts/smoke.py --record --turns 130
```

Evidence: `runs/20261030T233808.770370Z-142d95506cec/`.
It completed **130 turns, 129 turn-advancing actions, 132 decisions and 10 kills**.
Character creation, pause/resume/step and native anonymous spectating worked.
The **26.76-second** `gameplay.webm` excludes **12.01 seconds** of deliberate pauses
from 40.38 seconds of observed wall time. Chromium decoded playback and FFmpeg
decoded the entire file without errors. Metadata identifies `mibe` independently
of the selected `rule` engine.

Compared with the pre-refactor seed-8 run
`runs/20261030T231544.342144Z-5a874bedc331/`, both public starting-state and
130-turn trajectory hashes are identical. The trajectory hash is
`6429e758b6b9ad05a4ab76bafc2dad31ec21d69fac72dd1068ad38d1109da638`.
This checks the exercised opening, not universal determinism or improved survival.

Explicit installed-module loading and the independent engine override were exercised:

```fish
uv run --locked dcss-bot evaluate --character dcss_bot.characters.mibe:PROFILE --seeds 8 --agents rule dcss_bot.characters.mibe:RuleBasedAgent --repeats 2 --max-turns 130 --max-actions 600 --seconds-per-run 120
```

Batch: `runs/evaluations/20261030T233930Z-b0fbefaf/`.
All four trials completed 130 turns with 10 kills, verified fresh character/seed
evidence and matching public starts/RC. Each engine form repeated the same public
trajectory; the command returned `error: false`. Module provenance and recursive
package hashes are retained. This batch preceded the resume-parser correction below.

Real save resumption exposed a fixture error: native lobby links contain
`[Name, a level N Species Background of God]`, not `[MiBe, D:1]`.
The parser and regression fixtures now use the upstream format. The recorded
`Smoke16c7df97` save then resumed at turn 130, single-stepped to 131, resumed normal
execution, and saved cleanly at 133. Native spectating was visually checked again.
Evidence: `runs/20261030T234131.943219Z-3aa7fe911bb7/`; the preceding failed attempt
is retained at `runs/20261030T233930.832818Z-d18e21b625cc/`.
No MiBeBot save was used or changed.

The final unseeded smoke also exercised ordinary `dcss` mode, not just the seeded
entry:

```fish
env BOT_SEED= BOT_CHARACTER=mibe uv run --locked python scripts/smoke.py --turns 20
```

It completed **20 turns, 20 turn-advancing decisions and one kill**, including
pause/resume/step, then saved normally. Metadata confirms `game_id=dcss`,
`seed=null`, character `mibe` and engine `rule`.
Evidence: `runs/20261030T234515.399367Z-f906b5985aad/`.

## Limits and unverified paths

* Docker Compose and native host compilation were supplied but not executed here;
  the same Dockerfile and game configuration were verified under Podman.
* MacBook networking/SSH authentication remains to be checked by the operator.
* No LLM provider, model-server request, token/cost accounting, learning system,
  competitive tournament service, or exact deterministic engine replay is
  implemented. Sequential paired seeded evaluation and the strategy interface work.
* Opening-dungeon rule policy only: equipment selection and ranged tactics are
  limited, unknown menus can pause, and survival is not guaranteed. It does not
  claim advanced branches or winning play.
* Kill metrics are inferred from newly observed messages. Identical saturated
  message tails are ambiguous; final death cause is labeled by evidence source.
* Final joined WebM requires orderly shutdown. SIGKILL or host failure can leave
  partial capture files and completed `.video-*` segments rather than the joined
  output, plus a stale control lock. JSONL is flushed for inspection; a stale
  control socket is deliberately not stolen from another process.

Recommended next milestone: avoidance/escape planning for dangerous uniques,
visible distortion weapons and summoners, plus safe avoidance of inaccessible
enemies, checked on additional held-out seeds. Faster exploration is not itself a
survival improvement.
