# Verification and handover — 2026-10-30–31

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

## Survival-policy work — 2026-10-31

### Diagnosis and engine authority

The installed executable and source checkout identify unmodified DCSS **0.34.1**,
commit `1eebc1a2892e1c89776a0d7a10691f8dac8d9796`, save version **34.316**.
Policy facts were checked against that source, including native attack tables,
movement/pursuit, skill delay targets, Trog abilities, item labels and branch data.
The damage model is a conservative heuristic, not a combat simulator.

Five baseline development deaths were reviewed from complete public action traces
and native morgues. Recurring problems included prolonged retreat under pursuit
or ranged fire, failure to use known terrain/stair opportunities while clear,
premature Berserk before a summoner engagement, and weak handling of invisible
or distortion threats. Counterfactual survival is uncertain; the first enemy
discovery is not called a mistake when it depended on unseen information.
Postmortem consumables marked `{unknown}` were **not** identified emergency
resources available to the policy during play.

Real development execution also exposed nonstrategic defects: native equipment
menus use `use_item` even with input mode zero; right-clicking a monster standing
on items opens an examination-choice menu; skill rows can have a blank first
column; confirmation keys can require uppercase; and WebTiles map origins change
on level return/reconnection. Regressions use captured menu/encounter evidence.
Shared item normalization now preserves known terse brands and ordinary armour
egos while matching verbose operation menus. Wire subtype/plus/identification
flags remain excluded from policy observations.

Automated verification after these fixes:
`uv run --locked --extra video --group dev pytest -q` — **897 passed in 44.95s**.
This includes the twelve requested tactical scenarios, native item/menu boundaries,
actual loopback WebSocket reconnection, lifecycle controls and recorder behavior.
The scenario regressions are not claims that hydras or titanic slimes were reached
in the live opening runs.

### Exercised live boundaries

* `runs/20261031T022310.529164Z-4f06c04a86f2/`: fresh seed-8 MiBe, **160 turns,
  159 turn-advancing decisions, 171 decisions, six kills**; pause/resume/step
  verified. Chromium decoded the **39.04-second** WebM, excluding **12.011
  seconds** of deliberate pauses from **52.597 seconds** of observed wall time.
* The same disposable `Smokeb581530d` save resumed under the updated adapter:
  `runs/20261031T031159.839114Z-1ec59013af56/`, **30 additional turns, 30
  turn-advancing decisions, 34 decisions, four kills**. Controls verified again;
  **13.76-second** WebM decoded in Chromium and excludes **12.008 seconds** of
  deliberate pauses. Command:

  ```fish
  env BOT_SEED= DCSS_GAME_ID=dcss-seeded BOT_CHARACTER=mibe BOT_AGENT=rule uv run --locked --extra video python scripts/smoke.py --username Smokeb581530d --turns 30 --record
  ```

* Anonymous Chromium lobby selection and live gameplay were visually observed
  during `development5`: `Eval3f84b8519ecc4504`, D:1, visible time advancing from
  261.0 to 396.4, ordinary melee, Berserk expiry and exploration. Screenshot:
  `runs/spectator-video-evidence/20261031/development5-live.png`. This does not
  newly verify Firefox on the MacBook.
* `runs/ability-proof/20261031T015046Z/`: native Trog's Hand failed once at its
  displayed 3% failure rate, then succeeded on the next fresh decision; Regen/
  Will protection and overview Will increasing from zero to two pips observed.
  Brothers in Arms was unavailable at the character's piety: no live success
  claim is made for that ability.
* `runs/map-frame-proof/20261031T020318Z/`: D:1 → D:2 → D:1 produced a proven
  `(37,4)` map-origin translation with **1,929/1,929** remembered features
  matching; a subsequent move/reconnection produced `(1,0)`. The character was
  saved cleanly, without using a valued account.
* One attempt to resume an old development4 account stalled in an enchantment
  prompt authenticated and received the engine version, but timed out before
  any player observation. Evidence:
  `runs/native-scroll-recovery/20261031T022310.396862Z-87929e5982f8/`.
  A later public-protocol probe observed the saved character at turn 1633 with
  the native `Enchant which weapon?` menu already waiting. Startup incorrectly
  waited for another packet instead of handing that supported menu to strategy.
  After fixing this boundary, the **real saved menu was resolved**, the equipped
  hand axe was enchanted, **30 additional turns** ran and native saving succeeded:
  `runs/native-scroll-recovery/20261031T032129.050832Z-66cad490f0c1/`.
  Its **15.8-second VP8 WebM** was decoded and visually reviewed. No engine/save
  patch was used. The cause of the earlier pre-player timeout is not established.

All listed runtime artifacts are gitignored; historical paths do not guarantee
that a process is still running or artifacts exist in another checkout.

### Rejected development batch

`runs/benchmarks/mibe-survival/development5/` froze source and ran all eight
development seeds under the baseline's 8,000-turn / 20,000-action / 1,800-second
bounds. **All eight paused; none reached D:5**. Baseline development seeds reached
D:5 in three of eight games. Median maximum Dungeon depth fell from 4 to 2.5;
median XL from 6 to 4. This was a regression, **not improved survival** merely
because no character died before the pauses. The comparison is preserved at
`.runtime/development5-comparison.json`.

Review found a missing-data/interpretation problem: contact-only Freeze was
classified as a ranged attack, causing full-health endoplasm encounters to have
no candidate action. Other failures included a lone slow shooter outside the
closing HP gate, last-seen danger footprints trapping the player inside their own
blocked area, and unseen draining attacks being ignored when maximum HP changed.
One slowed halberd-gnoll encounter ended in a genuine dead-end without identified
resources; its final pause is not called a successful escape.

The native monster description already supplies coloured `range_string` labels
([describe-spells.cc](https://github.com/crawl/crawl/blob/0.34.1/crawl-ref/source/describe-spells.cc)).
The adapter now retains those displayed spell ranges, not monster HD or hidden
spell power. Source-verified contact/short-range fallbacks cover older observations
without that field. Public physical attack tables supersede the generic unknown
creature allowance, and displayed spell-damage dice are retained in the estimate.

### Repaired encounter and stair smoke

The contact-spell correction was exercised against the actual paused development5
seed-3 save, not just a fixture:
`runs/native-range-proof/20261031T034611.491194Z-0b8f6674ca3e/`.
It advanced from turn **144 to 264**, with **119 turn-advancing decisions**,
observed native **Freeze range 1**, and saved normally. Its **26.7-second** VP8
WebM was decoded and visually reviewed. This continuation is not a fresh paired
benchmark trial.

Development6 still paused in all eight games before D:5. The remaining gaps
included ordinary mixed ranged/melee groups with no candidate, slowed beetle
encounters, and a support caster whose generic unknown damage allowance overrode
its displayed attack table. The next policy adds bounded single-action engagements,
separate displayed attack/travel speeds, bounded nearby Berserk commitments and
fire-shedding movement that can preserve a safe Berserk approach.

The old reaching-gnoll dead end also exposed an omitted option: a known return
stair. Source verification distinguishes **one player-speed departure tick** from
the **1.5-player-speed arrival tick**, and followers are tagged at the start, not
after an approaching monster reaches the player.

Real proof: `runs/stair-escape-proof/20261031T042446.597500Z-bd0511e63b81/`.
The disposable development5 seed-8 character was resumed at **D:2, turn 965,
HP 23/39, slowed**, with a halberd gnoll at reach. Only its own recorded public
stair departure/arrival and last snapshot were restored to policy memory; no save
or hidden map was read. The bot inspected the visible gnoll, chose
`escape_upstairs`, and arrived on **D:1 at turn 967, HP 24/39**, with **no visible
hostiles**. The transition cost **37 aut**, matching slowed native timing.
It continued to **turn 1025, HP 39/39**, and saved. Its **26.8-second** VP8 WebM
was decoded and visually reviewed; the first seconds show the D:2 examination
and return to D:1. `smoke.json` records the measured result. This proves that
specific escape, not that all stair destinations are safe or that ordinary
process restarts automatically restore tactical history.
This is historical evidence, not the current policy's safety recommendation.
Its quantified departure burst was 36 against 23 HP. The current guard refuses
that potentially fatal departure; the positive regression now uses full 39 HP,
and a separate captured regression preserves the low-HP refusal.

Benchmark report schema 2 records its analysis-script hash and normalizes Berserk
HP scaling before counting dangerous net HP drops. Draining remains actual harm;
regeneration can still mask damage. Both benchmark sides must use the same
analysis version. The revised reporter was exercised on real seed-3 traces;
its focused suite passed **34 tests**.

### Further rejected development and native diagnosis

Development7 reached D:5 in **0/8**: **seven pauses and one D:2 death**. Median
maximum depth was **3**, maximum **4**; median XL **4**, maximum **6**. Median
observed turns were **2,344**, with pauses censored. This is still a regression,
not evidence of improved survival.

The death review examined both D:2 entries and the successful intervening stair
escape. At **sequence 1131, turn 1086**, the character reached a one-front
corridor at full **31/31 HP**, with Berserk available, then fought the known
ordinary gnoll queue without using it. The restrictive group/visibility gate
was an earlier avoidable candidate than the final 1-HP walking attack of
opportunity. Native morgue confirms a **gnoll's +0 flail, 5 damage**, at turn
**1101**; no carried consumables existed. Early Berserk is a credible alternative,
not a replay-proven survival counterfactual. Review:
`.runtime/development7-reviews.json`.

Other captured failures were implementation/model errors, not missing exotic
monster rules:

- A hand axe stayed visible from one waypoint but not the next; visibility-only
  loot routing oscillated with exploration. Remembered unprocessed goals now
  survive that LOS change.
- Native **whip6 + stones2** was summed into an eight-damage projectile.
  Physical melee rounds and alternative projectiles are now separate.
- The bot invoked Berserk, then alternated retreat/approach beside a trivial
  kobold. Pinned `main.cc:2844–2878` applies increasing nonattack duration penalties;
  `fight.cc:596–601` resets them on attempted attacks. Ordinary rage now attacks
  rather than waste that commitment.
- A maintained pursuit gap was labelled ineffective after two steps. Withdrawal
  now has a retained known goal and a moving energy forecast, with real progress
  checks rather than disabling the supervisor's cycle guard.
- Native Sigmund showed **17 + 26 distortion** in melee, but **3d4 Magic Dart /
  3d5 Throw Flame**. His scythe damage was incorrectly reused at range.
  The ranged maximum is **15**, displayed-dice mean **9**. Summoning/support
  spells also no longer become imaginary direct-damage beams.

Real repaired weak-encounter continuation:
`runs/native-rage-range-proof/20261031T050357.767885Z-d0ea13d28818/`.
The saved slowed character advanced **turn 299→359**, killed the kobold in three
melee attempts, recovered **21/26→26/26 HP**, and saved. The **31.4-second** VP8
recording was decoded and visually reviewed. It is a continuation, not a new
paired trial.

An initial real Sigmund continuation still paused at turn **2885** despite the
damage correction:
`runs/native-pursuit-proof/20261031T051025.134888Z-8e531924e671/`.
Public-history reconstruction revealed a **165-step visited return route**, while
the initial planner capped routes at 32 steps. Search now supports bounded
whole-level return routes, and can score one executable movement when a longer
forecast rejects the route; each next action still requires a fresh observation.
The regression preserves the actual public traversal, not a fabricated shortcut.

### Development 8: still not a demonstrated survival improvement

All development seeds remained separate from the held-out evaluation set.
Identical turn/action/time limits apply; pauses are censored outcomes, not wins.

| Metric | Baseline development | Development 8 |
|---|---:|---:|
| Games | 8 | 8 |
| Reached D:5 | 3 | 1 |
| Reached D:10 / Lair | 0 / 0 | 0 / 0 |
| Median / maximum dungeon depth | 4 / 6 | 4 / 6 |
| Median / maximum XL | 6 / 7 | 4 / 6 |
| Median observed turns | 2518.5 | 2599.5 |
| Deaths / safety pauses | 5 / 3 | 1 / 7 |

Censoring dominates; this pilot does **not** establish improved survival.
Its seed-8 morgue records a gnoll's +0 flail dealing 10 lethal damage on D:2,
turn 921. The earliest clear avoidable mistake was resuming exploration at turn
906 after slowing ended but while `-Berserk` remained. A fresh pack appeared
before the ability recovered. Trog's Hand was used; Berserk becoming available
at the terminal state does not mean it was available throughout the encounter.
The remaining consumables were unidentified revelation/noise scrolls and an
unidentified attraction potion, not known unused healing or escape resources.
Waiting earlier is a defensible correction, not proof of counterfactual survival.

Other captured failures exposed unrecognised invisible Magic Dart messages,
prematurely completed cover goals, and withdrawal cancellation when a returning
unique received a new native monster ID. Regressions now preserve those public
observations. Fresh unseen harm adds to visible threat; it does not invent a
monster coordinate. Free menu inspection cannot renew old damage evidence.

The corrected schema-2 baseline observer counts **57** Berserk-normalised
dangerous HP drops across 38 baseline games, not the earlier raw-HP count of
234. Berserk HP rescaling is not incoming damage. The preserved corrected report
is `.runtime/baseline-current-observer.json`; older schema-1 damage counts must
not be compared with schema 2.

### Real cooldown recovery and limits of the Sigmund continuations

`runs/native-cooldown-proof/20261031T060741.086097Z-9723b890970a/` resumed the
disposable seed-4 weak-encounter save and advanced **1239→1399**, saving at
**39/39 HP**. It spent **13 quiet full-HP turns** waiting for `-Berserk` after
slowing ended, with **no exploration or descent during that cooldown**, then
resumed. The **64.0-second VP8 WebM** was decoded and visually reviewed.
This is a 160-turn continuation, not a fresh paired trial. Its first weak-enemy
wait preceded the final active-rage advance correction; that correction is
separately covered by the current regression suite.

Later Sigmund continuations advanced 14 turns
(`runs/native-pursuit-proof/20261031T052103.800184Z-6187774e05b0/`) and eight
turns (`runs/native-pursuit-proof/20261031T052933.651680Z-5ffb8d4423f8/`), but
still paused. They exposed the new-ID and intermediate-cover failures above.
The final continuation,
`runs/native-pursuit-proof/20261031T060812.020796Z-0a2269f131a3/`, resumed
already within two-tile scythe reach and remained paused without advancing.
These are **not completed Sigmund escapes**. Pinned `movement.cc:220–244` and
`mon-act.cc:3641–3693` permit reaching attacks of opportunity after pursuit;
loosening that guard merely to move the stalled save would hide real risk.
Sigmund's default two-tile reach is now known before the first inspection.


### Development 9: diagnosed progression blockers, not a successful benchmark

The completed pilot again observed D:5 on only one development seed and no D:10
or Lair. Seven safety pauses and one death are not evidence of better survival.
Its retained native spectator screenshots show real progression from D:1 to D:4
at XL5, not a mocked renderer:
`runs/spectator-video-evidence/20261031/development9-live.png` and
`development9-live-later.png`.

The largest new diagnosis was a stale withdrawal goal after successful upstairs
travel. It planned another retreat on each clear arrival, climbing back to D:1
and banning harmless reverse stairs. All three downstairs were physically
reachable in seed1, but the accumulated bans boxed in its healthy XL6 character.
The goal now completes on an observed floor transition; actual unsafe arrivals
and the real abandoned reverse link remain protected.

Seed5 had only 13 clear reachable squares because eight cloud cells cut access
to otherwise eligible alternative stairs. The bot now waits and reobserves under
the existing cycle bound instead of calling this permanent exhaustion.

Seed7's final recorded identify-menu choice actually succeeded; the subsequent
unrecorded-as-success `identify i:w` failed. Native global identification changed
the supply letters while leaving an old appearance label in the sparse update.
Known Identify now refreshes visible inventory before spending, reconsiders stale
identities, and retains strict operation source/target validation.

Seed2 died to Magic Dart after its retreat stepped into a newly approaching
iguana at turn2892 and then opened additional hostiles. The moving-route prefix,
cover routing and generic repositioning now reject newly reachable melee contact;
opportunity attacks are not restricted to enemies adjacent before movement.
Its heal-wounds potion and fog scroll were still unidentified, not known unused
emergency resources. Trog's Hand was used, including on the terminal action.

Seed8 attempted Berserk at turn1054 but the native ability failed. A still-healthy
single-front ordinary queue later excluded a retry; the per-hit gate now accepts
ordinary visible attacks up to18 with a30% unbuffed-HP reserve, retaining the total
risk, one-front, poison, special-threat and aftermath checks. Later, a throwing net
was treated as no legal action. Native movement is now an explicit struggle,
with emergency consumable precedence and no false movement-failure bookkeeping.
The saved net continuation remains a dangerous situation, not an assured rescue.


### Native repairs, bounded caster combat and genuine public-memory escape

All paths below contain `gameplay.webm`, `smoke.json`, full public observations
and action/outcome logs. Videos were decoded and visually reviewed.
These are saved-character continuations, **not additional paired trials**.

| Continuation | Observed turns | Final state | VP8 video |
|---|---:|---|---:|
| Native item identity repair | 1243→1323 | D:3, 31/31 HP, axe enchanted +0→+1 | 25.60s |
| Cloud-blocked alternative stairs | 1942→2022 | D:1→D:2, 39/39 HP, two terrain waits then routing | 31.12s |
| Net release | 1068→1071 | Three real struggles clear Held; 11 HP, still tactically paused | 2.60s |
| Bounded Natasha fight | 1262→1382 | Natasha killed, XL4, 39/39 HP, resumed exploration | 46.00s |
| Public-history summoner escape | 3101→3261 | D:4→D:3→D:4, Eustachio killed, 51/51 HP | 78.52s |

Paths:

- Item repair: `runs/native-development9-repairs/20261031T072212.638650Z-1769924d4677/`.
- Cloud route: `runs/native-development9-repairs/20261031T072330.292522Z-e0354a58990e/`.
- Net: `runs/native-development9-repairs/20261031T072212.630428Z-b2b66c7edb02/`.
- Natasha: `runs/native-bounded-encounters/20261031T081957.100290Z-fc134148316c/`.
- Summoner escape: `runs/native-public-history-escape/20261031T083552.046561Z-c143db681317/`.

Natasha's replay includes two actual native ability failures followed by successful
Berserk, committed melee, recovery and exploration. It does not claim the ability
succeeded merely because a command was attempted. Public approximate HP, movement,
damage and spell-book limits permit this fight without relaxing hard-control,
invisibility, blink or poison restrictions.

Native net release exposed missing ability data after a combat resume. A free
native ability read now distinguishes unknown from unavailable and refreshes on
piety-rank changes. A subsequent replay exposed a separate emergency backstep
branch wasting active rage; its captured regression now requires an actual attack.
Routine/free actions also observe before replacing world history, retaining
same-turn damage without extending unseen-attack timestamps or duplicating kills.

The first Eustachio continuation still paused after 15 turns at 50 HP:
`runs/native-bounded-encounters/20261031T081957.100179Z-daae42ec28f6/`.
Its new supervisor remembered only ten visited positions. Reconstructing **188
actually observed player positions**, aligned by agreeing unique ordinary-stair
anchors, made a 120-step known return route available on the exact same public
snapshot. No player-derived translation, hidden map or save editing was used.

The final summoner continuation restored only those own public positions and the
recorded clear ordinary return link from turns2753→2755. It then retreated,
fought interfering summons, ascended at3185 with 51/51 HP, and returned through
an alternative stair at3236. The abandoned D:3 reverse stair was feature44; the
new descent used feature42. It killed an ufetubus, drude, quokka, iron imp and
Eustachio. Minimum observed HP was26. This proves that particular escape/return,
not a general escape probability.

**Cold restart limitation:** pause/resume retains strategy memory; a new
supervisor does not automatically restore it from logs. The explicit history
reconstruction above is a diagnostic continuation, not an implemented automatic
restore feature. Keep this separate from fresh-game benchmark results.

**Additional pinned-version limitation:** native `tileweb.cc:1201–1202` exposes
Doom separately from ordinary status lights. `player.cc:8023–8044` adds a random
bane at100% and resets Doom; `dat/descript/status.txt:1197–1205` says XP cures
Doom/banes. The reviewed summoner video visibly reaches38% Doom. The tactical
model does not yet expose/model these secondary HUD fields; Doom is not
incorrectly treated as an instant-death or rest-cured status.


### Development 10: all-paused pilot rejected; native boundaries repaired

The preserved schema-2 comparison is `.runtime/development10-comparison.json`.
All eight paired development starts matched; none reached D:5. Zero deaths
reflect eight censored safety pauses, **not improved survival**.

| Metric | Baseline development | Development 10 |
|---|---:|---:|
| Valid starts | 8 | 8 |
| Observed D:5 | 3/8; 37.5% (95% Wilson 13.7–69.4%) | 0/8 (95% Wilson 0–32.4%) |
| Observed D:10 / Lair | 0 / 0 | 0 / 0 |
| Deaths / safety pauses | 5 / 3 | 0 / 8 |
| Median / maximum dungeon depth | 4 / 6 | 4 / 4 |
| Median / maximum XL | 6 / 7 | 4 / 6 |
| Median observed turns | 2518.5 | 2602.5 |
| Normalized dangerous net HP drops | 16 | 7 |
| Unchanged-state actions | 1 | 0 |

Turn totals include censoring and do not estimate eventual lifetime. The pilot
is worse on progression despite its lower observed damage/death counts.

The actual pauses exposed a reaching-enemy guard excluding the one-action
fallback, all-exit stair bans, discarded invisible-contact feedback, and physical
maxima used as mean damage. `attack.cc:918–944` verifies rolled natural, weapon
and enchantment damage. The revised model keeps burst maxima intact and retains
conservative means for missing tables. A native low threat tier cannot override
known high weapon damage. Brand proc rates and exact AC/EV remain approximate.

Completed disposable native continuations, not new paired trials:

| Boundary | Observed turns | Actual outcome | VP8 recording |
|---|---:|---|---:|
| Two-orc reaching stall | 2769→2929 | Five kills, 45/45 HP, saved | 68.08s |
| Gnoll/adder wave | 1256→1416 | Adder, four gnolls and dart slug killed; 45/45 HP, saved | 67.00s |
| Clear but banned stair | 3564→3596 | Thirty route moves, one 42→46 descent D:3→D:4, 51/51 HP, saved | 15.80s |
| Confirmed unseen contact | 2898→2919 | Wizard killed after 20 contact-backed attempts; 16/20 HP, drained, then false pause | 7.96s |

Paths:

- Orcs: `runs/native-development10-combat/20261031T092558.321325Z-6163f2ac3d27/`.
- Wave: `runs/native-development10-combat/20261031T092558.318661Z-672e533ad078/`.
- Stair: `runs/native-development10-stair-memory/20261031T092638.938218Z-5c805de2f34b/`.
- Contact: `runs/native-development10-unseen-seed5/20261031T092629.223710Z-9814edf6f328/`.

All four WebMs decoded in Chromium. The wave was also watched live anonymously:
the native surface showed XL5, full 45 HP and continued exploration. The stair
proof restored 3,700 actual public actions/3,701 snapshots and 498 actual D:3
positions; its final public map genuinely had no ordinary frontier/corner probes.
It did not invent exhaustion, remove emergency 43/44 bans, or edit the save.

The contact proof restored only the actual preceding public transition and used
a proven reconnect offset(-3,+14). Fresh player feedback renewed one attempt at
a time; killing stopped renewal. Its limited restoration reset the historical
ineffective-retreat counter to 0 rather than 2, so it is not exact strategy replay.
The low final maximum HP reflects severe draining, not a healthy rescue.

That false post-kill pause was then fixed using native unseen death feedback
(`mon-death.cc:309–311`), preserving fresh harm from any second hidden attacker.
Recorder kill totals now recognize that feedback without naming its victim or
recounting it during free UI. A real follow-up restored only the actual last
kill transition, continued 2919→2959, recovered to 20/20 HP and saved; draining
remained. Evidence:
`runs/native-feedback-followup/20261031T094527.782755Z-932abd468cdb/`.

The initial prepared Natasha continuation still paused after one turn at full 31 HP:
`runs/native-development10-combat/20261031T092558.318649Z-7c1fd6ba125f/`.
After the physical-mean correction and bounded prepared-caster approach, it ran
1344→1504, killed Natasha, quokka, endoplasm and ribbon worm, handled a net, and
saved at 39/39 HP (minimum 16). Evidence:
`runs/native-development10-combat/20261031T094527.767644Z-06e12a6046f0/`.
These successes justify fresh evaluation, not a win-rate or general-survival claim.
The follow-up's 16.12-second and corrected caster's 65.40-second VP8 WebMs both
decoded and were visually reviewed in Chromium. The caster frame also shows the
bounded quiet-ground unknown-scroll policy discovering native Teleportation;
this is not a claim that every emergency consumable was identified.

### Development 11: fresh failures, not a survival improvement

The preserved same-observer comparison is `.runtime/development11-comparison.json`.
All eight starts matched the frozen development baseline. All eight runs stopped
at pauses; none died. These are censored games, not successful escapes or wins.

| Metric | Baseline development | Development 11 |
|---|---:|---:|
| Valid starts | 8 | 8 |
| Observed D:5 | 3/8; 37.5% (95% Wilson 13.7–69.4%) | 1/8; 12.5% (95% Wilson 2.2–47.1%) |
| Observed D:10 / Lair | 0 / 0 | 0 / 0 |
| Deaths / pauses | 5 / 3 | 0 / 8 |
| Median / maximum dungeon depth | 4 / 6 | 2.5 / 5 |
| Median / maximum XL | 6 / 7 | 3.5 / 6 |
| Median observed turns | 2518.5 | 2080.5 |
| Normalized dangerous net HP drops | 16 | 7 |
| Unchanged-state actions | 1 | 1 |

Five tactical pauses, two item-action errors and one repeated-wait stop explain
the endpoints. Seed6's examined human zombie was behind eight currently visible
clear-rock walls; repeated waiting could never lure it. Seeds5/7 exposed inventory
category paging after global identification/relettering. Their precise failed
menu packets were not retained, so the inventory diagnosis is not a claim about
an unobserved rejected field.

Other fresh failures separated real danger from missing actions or bad estimates:
the seed3 distortion-scythe Sigmund remained a genuine melee prohibition; the
seed8 ordinary javelin thrower's displayed projectile maximum had incorrectly
become its adjacent melee damage; seed4's displayed small caster book included
a wand's Throw Flame; seed1 was confused while Berserk; and seed2 had fresh
unlocated melee damage after visible and unseen victims died. A named kill
must not erase that second attack. No held-out per-game trace was opened for
these development changes.

#### Native follow-on checks after the source11 failures

`uv run --locked --extra video pytest -q`: **950 passed in 45.57s** after the
historical-enclosure integration. Earlier 938-test verification covered the
nonmoving attack, inventory and damage-envelope repairs.

Six disposable saved-account continuations each completed another 160 game turns
and saved normally. They are not fresh starts or a new survival benchmark.

| Actual boundary | Turns | Observed result | Recording |
|---|---:|---|---:|
| Confused Berserk, unseen melee | 3966→4126 | Three stationary Ctrl-direction attacks, unseen kill and adder killed; 51/51 HP | 74.92s |
| Unlocated attacker after other kills | 2586→2746 | One stationary Ctrl-direction attack killed an unseen magic user; four total kills, 51/51 HP | 64.28s |
| Natasha with flame wand and bat | 2095→2255 | Bat, Natasha and hobgoblin killed; D:3, 45/45 HP, minimum 25 | 69.40s |
| Exhausted versus javelin thrower | 430→590 | Hobgoblin and six other enemies killed; 31/31 HP, minimum 23 | 62.24s |
| Paged Identify inventory | 1045→1205 | Autonomous `identify i:d` revealed Revelation; D:2, 39/39 HP | 114.84s |
| Unavailable Identify, usable enchantment | 1316→1476 | Autonomous `read w` selected weapon `a`; axe became +1; 31/31 HP | 104.96s |

The four combat recordings are in `runs/native-development11-combat/`:
`20261031T112208.329830Z-2d0710bf298a` (confusion),
`20261031T112208.330590Z-088e5846e3d2` (unlocated melee),
`20261031T112208.332252Z-fe2c013387ba` (Natasha), and
`20261031T112208.338319Z-eaf23aa7eb9b` (thrower).
Each contains `smoke.json`, joined actions/observations, summary and `gameplay.webm`.
The actual nonmoving attacks preserved player coordinates; no hidden location
was supplied. Axe-cleave feedback supports retrying a command, not identifying
its unseen victim or square.

Item proofs are under `runs/native-proofs/development11-item-menus/`:
seed 5 `20261031T112310.936685Z-2336b315c3f1`, and
seed 7 `20261031T112310.942543Z-252167624115`. Native public menu packets are retained.
Gear→Potions→Scrolls→Gear was observed in seed 5; empty categories were skipped
in seed 7. Its actual Read menu showed Identify `i`, quantity 1, but unavailable
(colour 8), while enchantment `w` was enabled. At turn 1181, inventory inspection
also corrected a relettered stale Revelation label without changing quantity.

All six VP8, 1280×960 WebMs decoded in Chromium. Three larger files reset an
initial seek over the temporary basic HTTP server; loading their complete local
data as browser Blobs gave seekable playback at 10s with no decoder errors.
Native gameplay and the Identify result were visually reviewed. This is not a
MacBook/Firefox playback or remote-access test.

The first enclosure continuation was **not successful**:
`runs/native-development11-enclosure-seed6/20261031T112237.584213Z-43a749cb3714/`
ran 3157→3181, then saved after a period-five loop. Its first bypass was legal, but
forgetting a boundary wall as it left LOS caused wait/approach/bypass repetition.
That trace supplied the historical-memory regression; the failed video is retained.

The recorded historical-memory recheck cold-resumed that same account at its
actual saved turn 3181, without seeded policy history:
`runs/native-development11-enclosure-seed6-recheck/20261031T113419.977605Z-7a4adfb7823d/`.
Three bounded waits and one approach reacquired all eight visible boundary walls
at 3185. The first westward bypass lost visibility of three walls at 3186; the
next westward step used certificate age 1 without changing their visibility.
The zombie then left LOS and autonomous exploration/combat continued.

It reached turn 3338, D:4, XL6, 51/51 HP with three recorded kills, then safely
paused at a separate Eustachio encounter. No period-five loop or recorded error
recurred. The 157 elapsed turns included 146 turn-advancing decisions, 74 position
changes and 59 wait actions; waiting is not presented as tactical progress.
The 49.2-second VP8 WebM decoded and was visually reviewed in Chromium.
This establishes the enclosure transition, not successful handling of Eustachio
or a new-game progression result.

Fresh development12 play was also watched through the anonymous native lobby:
`Eval2118dd9136574fbb` was visibly active at D:2, XL5, 45/45 HP with a +0 hand axe.
The spectator used neither login credentials nor gameplay controls; its browser
tab was closed afterward. This surface check does not change the benchmark's
game actions or establish an aggregate outcome.

### Development 12: protocol errors removed, progression still below baseline

`.runtime/development12-comparison.json` preserves the same-observer paired
comparison. All eight starts matched; all eight ended at safety pauses, not
deaths or wins. No action/protocol errors or unchanged-state actions were recorded.

| Metric | Baseline development | Development 12 |
|---|---:|---:|
| Observed D:5 | 3/8; 37.5% (95% Wilson 13.7–69.4%) | 2/8; 25% (95% Wilson 7.1–59.1%) |
| Observed D:10 / Lair | 0 / 0 | 0 / 0 |
| Deaths / pauses | 5 / 3 | 0 / 8 |
| Median / maximum dungeon depth | 4 / 6 | 4 / 5 |
| Median / maximum XL | 6 / 7 | 5 / 6 |
| Median observed turns | 2518.5 | 2795 |
| Normalized dangerous net HP drops | 16 | 10 |
| Unchanged-state actions | 1 | 0 |

The result still does not demonstrate improved survival/progression. Endpoint
review separated several causes rather than weakening every safety pause:

- Seed1 reached D:5, then exhausted banned D:4 returns. Actual stair42 departure
  was an emergency; stair44 was quiet but one HP short of full; stair43 still had
  Slow/cooldown and recent unseen combat. None supplies the required clear
  departure receipt. A later full-health endpoint cannot retroactively create one.
- Seed4 had mild poison (27 HP, native poison survival25) and six ordinary
  enemies. A categorical poison fallback ban combined with Ensnare being treated
  as damaging ranged melee. Native Ensnare does zero direct damage and creates a
  web; its movement risk is not prevented by Will.
- Seed5's last incoming attack was at2797, followed by two quiet moves at full
  current HP. The old danger window needed one more observed world turn to
  expire; pausing prevented that transition. Earlier actual shield-block messages
  also supply contact evidence without HP loss.
- Seed8's plain-whip gnoll was across deep water and a partially observed wall
  pocket. Hidden interior/boundary cells prevent a complete-cage certificate.
  Known barriers can still bound walking for the next few actions if unknown
  cells remain optimistically passable; that is not proof of permanent trapping.
- Seeds6/7 stopped at Eustachio; source6 also included the observed sealed zombie.
  Summoning-only pressure was absent from the corner candidate, while
  wall-independent travel overestimated the zombie's physical pursuit.
- Seed2 stopped at25/45 HP with goblin/bear threats; seed3 stopped at22/31 HP
  near distortion-scythe Sigmund. Their summaries alone do not prove the earliest
  avoidable mistake or justify forcing a melee attack.

Held-out per-game traces remain sealed. The development fixes are not presented
as a held-out evaluation or as evidence of a three-rune win rate.

### Development 13 follow-on checks before the next fresh benchmark

`uv run --locked pytest -q`: **989 passed in 46.23s** after the source12
geometry, web-control, poison, unseen-signal, stair-preparation and ability-cache
repairs. This result precedes the later loot-cycle/summoner follow-on edits.

The actual seed1 ability-inspection trace exposed a state error, not merely an
overcautious strategy. At turn3868 the status was native `Conf`; the old guard
matched “confused” but not that abbreviation. The native menu correctly returned
Renounce Religion, Berserk and Trog's Hand as unavailable. That transient cache
was still present at the quiet turn3884 and stair return3912. New guards defer
the read during restrictions and refresh after recovery or exhaustion expiry.
`ability.cc:1809–1833,4218–4222` verifies the native availability behavior.

Six recorded disposable saved-account checks used cold current-policy state,
not restored tactical history, altered saves or new paired trials:

| Source seed | Saved turn → final turn | Turn-advancing decisions | Result | Final HP |
|---|---:|---:|---|---:|
| 1 | 4008 → 4168 | 159 | 160-turn guard, saved, D:5 | 51/51 |
| 4 | 1271 → 1431 | 159 | 160-turn guard, saved, D:2 | 31/31 |
| 5 | 2799 → 2959 | 160 | 160-turn guard, saved, D:4 | 42/42 |
| 6 | 3431 → 3451 | 20 | Tactical pause, saved, D:4 | 49/51 |
| 7 | 2791 → 2951 | 160 | 160-turn guard, saved, D:4 | 48/51 |
| 8 | 3450 → 3494 | 34 | Period-five policy cycle, saved, D:5 | 51/51 |

Artifacts under `runs/native-development13/`:

- Seed1: `20261031T131422.907802Z-5a1af04dfd79/`
- Seed4: `20261031T131215.271248Z-fd19e83486eb/`
- Seed5: `20261031T131214.185534Z-90c6d99c158f/`
- Seed6: `20261031T131214.189092Z-c0271eb172ca/`
- Seed7: `20261031T131214.189579Z-ad91b1ca547f/`
- Seed8: `20261031T131217.951568Z-691fa2aabc1e/`

Each retains public observations/actions/events, summary, smoke report and WebM.
All six videos passed FFprobe as VP8,1280×960. Poison and Eustachio footage also
decoded in Chromium with readyState4/no media error; actual native gameplay
frames were visually inspected, then both playback tabs were closed.

Seed4 performed bounded nonrage action while native poison survival remained25,
then fought, recovered and explored; seven kills. Later rage/web/exhaustion left
only11 HP before recovery, so this is not a claim of optimal commitment.
Seed5 genuinely encountered the unseen wizard again, used Hand, observed native
shield blocks and killed it before continuing; six kills. Because this was a
cold run, it does **not** isolate expiry of the old2797 contact signal or the
full-HP shield-only preparation boundary.

Seed7's new cover steps broke the former deadlock; it used might, killed
Eustachio, reached XL5 and continued, recording22 kills. Seed6's initial cover
also worked, but a later high-HP distance-two minor-summoner encounter exhausted
retreat and paused. Seed8's first finite-barrier moves were dry and preserved
gnoll observations; later equipment pursuit repeatedly reversed that bypass.
Those two actual failures, not a higher loop limit, supply the next regressions.
No native errors were recorded in these checks. Their capped/paused endpoints
do not demonstrate improved survival or a win rate.

#### Targeted rechecks with actual public-history restoration

The integrated policy passed **1017 tests in47.72s**. Unlike the cold checks,
these two rechecks reconstructed actual prior public observations/actions before
reconnecting to their own saved accounts. No policy decisions or game inputs
were made during restoration; no unvisited positions or hidden cells were added.
The optional loot destination was inferred only where the route's first
direction matched the actual recorded equipment move.

- `runs/native-development13-recheck/20261031T132732.474442Z-21e59b776846/`:
  restored three actual ineffective retreats, then ran3451→3496,45 advancing
  decisions,11 kills and saved at40/51 HP. Native Hand succeeded3451→3452,
  healing49→51 with regeneration/Will messages. Eustachio approached into melee
  and was attacked; later Blink and multiple summons prolonged the fight.
  The sole bounded approach3478→3479 executed, then Eustachio blinked. The later
  pause involved shadow imp, ufetubus, drude, Eustachio and the separated zombie;
  this was not the original high-HP single-summoner deadlock.
- `runs/native-development13-recheck/20261031T132732.473022Z-13c9c2cc9ace/`:
  restored46 actions and seven compatible equipment routes, including the actual
  rejected target`(-3,12)`. It ran3494→3500, six advancing decisions, without
  reopening that optional loot cycle. Three real exploration steps then revealed
  a different gnoll carrying a pain halberd and throwing net; the next pause was
  a new tactical boundary, not another period-five cycle. HP remained51/51.

Both saved normally. FFprobe verified VP8,1280×960, durations24.96s/7.48s.
Neither completed100 follow-on meaningful turns, so they are mechanism checks,
not sustained-play successes. Frozen package hashes in
`runs/native-development13-recheck/frozen-policy/manifest.json` exactly matched
the subsequently launched fresh development13 batch. No aggregate improvement
claim follows from these capped/paused continuations.

### Fresh development 13: progression tied, one actionable death

The eight fresh games completed normally under the same 8,000-turn,
20,000-action and 1,800-second per-worker bounds. Comparison:
`.runtime/development13-comparison.json`; scoped death annotations:
`.runtime/development13-death-reviews.json`. All eight paired starts matched.
There were seven tactical pauses and one death, no forced shutdowns and no
recorded protocol/action errors.

| Observed development metric | Frozen baseline, seeds 1–8 | Development 13, same seeds |
|---|---:|---:|
| Reached D:5 | 3/8 | 3/8 |
| Reached D:10 / Lair | 0/8 / 0/8 | 0/8 / 0/8 |
| Deaths / pauses | 5 / 3 | 1 / 7 |
| Median / maximum depth | 4 / 6 | 4 / 6 |
| Median / maximum XL | 6 / 7 | 5 / 7 |
| Median observed turns | 2,518.5 | 2,538.5 |
| Dangerous HP-drop flags | 16 | 7 |
| Recorded consumptions | 0 | 10 |
| Retreat episodes / endpoints without visible hostiles | 144 / 68 | 518 / 345 |

D:5 attainment in either cohort was 37.5%, Wilson 95% interval
13.68–69.43%. Two seeds reached it in both policies, one only in each policy,
and four in neither: paired difference **zero**, not an improvement.
The increased pause rate censors subsequent survival and progression.
Retreat endpoints are visibility observations, not confirmed escapes; the large
episode count is also a liveness concern.

The ten observed consumptions were three teleportation scrolls, three identify
scrolls, and one each of unknown scroll, poison scroll, enchant weapon and might.
The unchanged-state metric recorded zero repeats, but missed the fatal case:
damaging attacks were changing HP/messages while intended movement made no
position progress.

Seed 2 died on D:4, XL5, turn 2479. The earliest **confirmed** avoidable error was
the repeated northeast retreat at turn 2472, after the turn-2471 command had
already failed to move and produced anonymous outgoing melee feedback.
Four attempts left the player on the same square while HP fell 26→4.
The review identifies turn 2472 / action sequence 2569, not the final attack.
The final public message was an unseen puff of flame; this is a
`last_damage_message` label, not a retrieved morgue identity.
No identified emergency consumable was available in the recorded inventory.
Earlier Berserk cannot be blamed on the later unseen wizard as if that enemy
had already been known.

### Follow-on mechanism checks and development 14 preparation

A frozen intermediate 1,049-test policy was checked against three disposable
development-13 saved accounts, with videos under
`runs/native-development14-preview/`:

| Source seed | Preview run | Native turns | Endpoint |
|---|---|---:|---|
| 1 | `20261031T141752.050220Z-5c28554e1f7f` | 4491→4501 | D:6 pause, 23/51 HP |
| 4 | `20261031T141752.053381Z-438e97551354` | 1996→1996 | D:2 pause, 39/39 HP |
| 8 | `20261031T141752.050551Z-0b4a7cabb578` | 3409→3421 | D:5 pause, 57/57 HP |

These were cold mechanism checks, not new paired trials or sustained-play
successes. Seed 4 completed turn-free native examinations of Natasha, bat and
cerulean imp; its bat was three squares away, **not adjacent**. Seed 1's final
centaur remained seven squares away. Seed 8 retained the net's zero HP damage
and real entangling capability; its short lure/explore cycle was not a safe
permanent enclosure. All three saved normally. FFprobe verified VP8, 1280×960,
with durations 8.36, 4.04 and 8.84 seconds.

The next integrated policy adds fresh failed-displacement evidence, finite
public-only shooter approaches, forecast-based ranged exposure, corrected
lava/deep/shallow terrain IDs, and finite net-projectile barrier certificates.
Native physical-projectile means are no longer identical to their displayed
maxima; maxima remain separately preserved.

The actual seed-4 D:2 observation also isolated a blanket retreat veto:
the planner returned no route with its examined 1-damage, 300%-speed bat, but
returned routes on identical terrain with only that bat synthetically slowed
or removed. These control variants are **not native gameplay outcomes**.
The correction permits at most one fully examined weak plain-melee interceptor
per forecast step and charges both regular attack exposure and possible
opportunity attacks. Strong, reaching, unknown, bonus-effect, spellcasting,
poison/control threats, multiple interceptors and low-HP exposure retain vetoes.
On the unchanged actual observation, a one-action route toward nearby apparent
cover then forecast 1.31 expected / 13 maximum-envelope HP loss, including the
caster and possible bat opportunity strike. This is an uncertain forecast,
not proof of safety or survival.

Regression fixture:
`tests/fixtures/recorded_development13_bat_retreat.json`. Only public known
terrain and actual visits within ten squares were retained; actor descriptions
and player state are unchanged. No future game responses are replayed.
After integration, `uv run --locked pytest -q` passed **1,186 tests in 53.88s**.
The same package was frozen for fresh development 14 while the integrated suite
completed, with the same bounds, eight workers, and recording for seeds
1, 2, 4 and 8. No improvement claim follows from previews or regression count.

### Rejected development 14: native client-ID aliases blocked ordinary combat

`.runtime/development14-comparison.json` preserves all eight matched starts.
Every run tactically paused and saved; no forced shutdown, death or recorded
protocol/action error. Each evaluator correctly returned failure (exit 1).
Observed D:5 attainment regressed from baseline 3/8 to **0/8**; maximum depth
was D:2, median depth D:1, median XL2 and median observed turns359.5.
Zero dangerous HP-drop flags and zero deaths are **not survival improvement**:
the policy stopped playing before ordinary encounters were resolved.

Seven terminal decisions were examined directly. Each retained exactly one
unresolved name count, no restrictive statuses, no unseen displacement evidence,
and the same generic shooter rejection. No bounded approach had ever executed.
The new gate incorrectly treated any old native ID other than the current ID
as proof of an additional unseen creature. In the actual seed-4 trace, the
currently visible kobold changed ID3→4 across turns40→41, lost its cached
description and was freely reexamined; later visible IDs reached9 while the
observed unresolved kobold count remained1.

Native authority is explicit: `player-notices.cc:551–557` resets a monster's
client ID outside seen cells specifically so the player cannot know for sure
whether it has been seen before. `monster.cc:234–242` clears and reallocates the
opaque ID. Therefore old IDs prove neither an extra body nor persistent entity
identity. The correction must preserve the encounter allowance without claiming
identity or refunding actions/time, and retain real multiple-body/count gates.
Missing descriptions require fresh public examination, not invented data or a
permanent cancellation merely because the cache is absent.

A separate direct-planner check on seed1's healthy paused slug found that a
whole eight-neighbor visibility-ring requirement rejected an otherwise visible
clear route. Route cells themselves must remain visible and legal; requiring
every side cell visible is stronger than refusing travel into unknown terrain.
These diagnoses motivated the next policy, not a higher pause/loop limit.
The rejected cohort remains intact; held-out traces were not opened.

### Corrected native-ID handling: sustained saved-encounter checks

The follow-on package is frozen at
`runs/native-development15-check/frozen-policy/manifest.json`. These three
disposable development-14 saved accounts were reattached cold, with an exact
starting-turn guard before gameplay input. No tactical history, hidden positions
or altered saves were supplied.

| Source seed | Native-check run | Turns | Advancing decisions | Recorded kills | Final HP / XL |
|---|---|---:|---:|---:|---|
| 1 | `20261031T153651.683143Z-a4742786eeed` | 423→583 | 155 | 1 | 26/26 / 2 |
| 4 | `20261031T153651.680943Z-bf39a2172370` | 50→210 | 158 | 2 | 19/19 / 1 |
| 5 | `20261031T153651.680871Z-25b53a58e402` | 190→350 | 150 | 9 | 31/31 / 3 |

All three executed a bounded shooter approach, resolved combat, resumed
exploration, reached the 160-turn guard and saved normally rather than repeating
the prior early pause. Seed5 reached D:2; the other endpoints were D:1.
These are sustained mechanism checks, **not fresh paired outcomes** or evidence
of improved milestone rates.

Each directory under `runs/native-development15-check/` retains summary,
`smoke.json`, public action/observation/event logs and `gameplay.webm`.
FFprobe verified VP8, 1280×960, with durations68.68s,62.80s,67.44s respectively.
Seed5's clip decoded and sought to5s in Chromium: readyState4, no media error,
actual native tiles and the wounded-slug melee messages were visually inspected.
The playback tab was closed.

The integrated regression suite passed **1,219 tests in55.76s**. Captured
source4 ID transitions/free reexamination and source1/source4 terminal geometry
are preserved in
`tests/fixtures/recorded_development14_shooter_aliases.json`; synthetic controls
are explicitly labelled. Tests cover unchanged encounter allowances across
advisory aliases, missing descriptions, real additional counts, time/HP limits,
proven/unproven rebases, open doors and illegal route destinations.

Fresh development15 was launched separately, under the same bounded eight-seed
configuration, recording seeds1,2,4,8. During that cohort an **unauthenticated**
Chromium observer selected `Eval2dd7ddbf8b92423b` in the native lobby and reached
its actual `#watch-...` surface: title “the Chopper”, Minotaur of Trog, XL3,
31/31 HP, native dungeon canvas1080×1000. The rendered map/sidebar were visually
verified and the tab closed. Earlier loading-screen-only captures were not
counted as gameplay proof. This verifies Linux Chromium spectating, not a new
MacBook/Firefox or remote-tunnel test.

### Development 15: corrected aliases, but still no progression improvement

`.runtime/development15-comparison.json` contains eight matched starts, all
tactically paused and saved without forced shutdown or recorded protocol/action
errors. D:5 attainment was12.5%, Wilson95% interval2.24–47.09%; D:10 and Lair
were not observed.

| Development metric | Frozen baseline | Development 15 |
|---|---:|---:|
| Reached D:5 | 3/8 | 1/8 |
| Deaths / pauses | 5 / 3 | 0 / 8 |
| Median / maximum depth | 4 / 6 | 3 / 5 |
| Median / maximum XL | 6 / 7 | 3.5 / 7 |
| Median observed turns | 2,518.5 | 2,132.5 |
| Dangerous HP-drop flags | 16 | 8 |
| Retreat episodes / no-visible-hostile endpoints | 144 / 68 | 586 / 436 |
| Recorded consumptions | 0 | 14 |

This cohort is rejected: lower death/drop counts reflect informative pauses,
not demonstrated survival. The large retreat count is not a success metric.
The native-ID fix did permit ordinary combat, but other policy interactions
still prevented progression.

#### Actual movement receipts versus later decisions

Seed3 executed approach1802 atturn1747, moving`(0,0)→(1,0)` toward the examined
kobold. The next decision1803 deliberately retreated back to`(0,0)`.
The tracker then reinterpreted that later position as a second result of the
first move and falsely cancelled it as failed displacement.
`recorded_development15_shooter_reversal.json` preserves these actual boundaries,
with public terrain clipped to nine squares. Historical responses are not
presented as a new-policy rollout.

The correction consumes each issued movement receipt once. A still-admitted
active approach must not be reversed by optional return movement merely because
its old stair is one square behind. Emergency resources, new foes, changed
mechanics/status, HP admission and finite action/time budgets still preempt it.
An admitted manageable lone shooter is also not itself a reason to evacuate a
floor. Refused approaches and genuinely dangerous encounters retain defensive
routing. The single-attack reserve uses the actual proposed position rather than
charging a ranged attack that an ordinary thrower cannot make in melee. Absolute
multi-attack bursts remain separately logged uncertainty, not a survival guarantee.

#### Quiet post-ascent pauses were not permission to erase stair bans

Four endpoints were healthy with no visible enemies and normal command input:
seed1 turn3609 onD:3 afterD:4; seed2 turn1132 onD:1 afterD:2; seed6 turn3987
onD:4 afterD:5; seed7 turn2622 onD:3 afterD:4.
Chronological review, not cold final snapshots, found no eligible unspent clear
receipt. Seed1 had spent prior probes and its apparent unspent departure had
only eight quiet turns. Seed6 had only five quiet turns after a wizard, including
an actual unseen puff of flame that hit for zero HP damage while Hand was active.
Its full HP and beneficial regeneration were **not proof of a clear departure**.
Seed2 had spent its valid probe; seed7's three returns were actual emergency
escapes. No speculative near-full-HP/beneficial-status relaxation was applied.
The earlier excessive tactical evacuations must be prevented instead.

#### Menkaure commitment was a different contradiction

Seed5 used Berserk2653 atturn2508 against an examined black bear. Menkaure was
first exposed2655 atturn2510 and examined2656: the policy did **not** know about
him before rage. The bear's native self-enchantment spelling “Berserker Rage”
was missing from the self-spell classifier, creating fictitious ranged pressure;
fixing that interpretation does not establish a different native outcome.

The first confirmed subsequent mistake was2656: a blanket avoidance flag forced
retreat toward the newly visible Menkaure instead of finishing the almost-dead
adjacent bear. Later deliberate approaches reached Menkaure, but the same flag
then prohibited attacking him while Berserk and unable to use resources.
The next policy separates hard harmful melee mechanics from an ordinary
high-risk caster preference for already-committed, publicly examined fights.
Hydra/head-growth, escalating slime, dangerous-weapon and axe-cleave guards
remain hard overrides. No successful native Menkaure outcome follows merely
from these captured regressions or the code change.

### Development 16 mechanism proof and checked candidate

The intermediate frozen package at
`runs/native-development16-check/frozen-policy/manifest.json` was exercised
against the two saved development-15 encounters, cold and with exact starting
turn guards:

| Source seed | Native run | Turns | Advancing decisions | Kills | Final HP / place |
|---|---|---:|---:|---:|---|
| 3 | `20261031T163002.044593Z-64ec4b7318b0` | 1749→1909 | 158 | 4 | 31/31, D:2 |
| 5 | `20261031T163002.042739Z-cfe823ef0512` | 2516→2676 | 155 | 2 | 51/51, D:4 |

Both reached the160-turn guard and saved normally. Seed5 actually recorded
**“You destroy Menkaure!”**, then recovered and resumed exploration.
Its VP8 1280×960 video,60.40s, decoded/seeks in Chromium with readyState4 and no
media error. The1.5s frame visually showed actual native Berserk melee against
the wounded Menkaure; the kill is established by the native message log, not
invented from that earlier frame. Seed3's video passed FFprobe at76.12s.

This intermediate variant priced the next action by its full absolute maximum.
That overstrict admission change was rejected by37 existing regressions,
including healthy ordinary-centaur approaches. The checked candidate retains
the original single-attack reserve but makes it position-specific: ordinary
throwers cannot be priced as firing a javelin at adjacency. Full-route means,
single-attack reserve and logged absolute multi-attack bursts are different
quantities; none is an exact survival probability or guarantee.

The resulting candidate passed **1,258 tests in53.47s** and is frozen at
`runs/native-development16-recheck/frozen-policy/manifest.json`. Two further
disposable saved development-14 accounts exercised that exact candidate:

| Source seed | Recheck run | Turns | Advancing decisions | Kills | Final HP / place |
|---|---|---:|---:|---:|---|
| 2 | `20261031T163929.169784Z-7cc380f3bd37` | 925→1085 | 142 | 3 | 26/26, D:2 |
| 6 | `20261031T163929.172573Z-62113fae46c4` | 690→850 | 160 | 3 | 26/26, D:1 |

Both immediately executed a bounded approach rather than an unnecessary
planned retreat; combat/exploration then continued. Seed6's first approach
cost one HP, followed by two combat decisions and recovery. Neither recheck
logged a planned-retreat action. Both saved at the160-turn guard.
FFprobe verified VP8,1280×960, durations66.84s/84.56s. These capped cold
continuations prove mechanisms, not fresh milestone attainment or a win rate.

`tests/test_mibe_development15_stairs.py` additionally preserves the distinct
chronological spent/revoked-receipt and no-damage unseen-attack boundaries.
The disconnected public stair in seed6 is not made reachable by deleting
tactical avoidance. No production stair relaxation was justified or applied.

Fresh development16 was launched from the checked frozen source, under the same
eight-seed bounds, with recording for1,3,5,8. Its fresh outcomes must be compared
separately before choosing the final held-out policy. Held-out traces remain
unopened at this stage.

### Development 16 outcome and next-reaction formation correction

Fresh development16 finished eight valid, common-schema-matched starts:
**2/8 observed D:5, one death and seven safety pauses**, versus baseline
development's3/8 D:5, five deaths and three pauses. D:10/Lair remained unobserved.
Median/max depth3.5/6, XL3.5/7 and observed turns2313/4684 are capped/censored
observations, not lifespans. Five normalized dangerous HP drops and256 retreat
episodes (158 ending without visible hostiles) do not demonstrate improvement.
The report and scoped reviews are `.runtime/development16-comparison.json` and
`.runtime/development16-death-reviews.json`.

The seed3 death was reviewed chronologically, including earlier recovered fights.
The earliest confirmed formation mistake was action2127/turn2051: full31HP,
six already-public bodies, eastward advance from(0,13) to(1,13). The lizard,
hobgoblin and goblin could each arrive adjacent in the next normal reaction;
the completed2052 observation actually had all three adjacent. A damage-only
forecast/current-contact count missed that commitment. Sigmund and Robin were
not yet discovered and are **not back-projected** into the decision.
The later anonymous final hit/space-warp messages do not prove the killer's name.
Captured regressions now choose one bounded formation-preserving hold; survival
after that counterfactual hold is not established.

The fallback now counts optimistic next-reaction contact fronts using public
movement/reach and known barriers. Holds/ordinary approaches retain a two-front
cap. A distinct, tighter poison-reserved defensive step may reduce several
standing arrivals to at most one without approaching an enemy. In the captured
mild-poison six-body case, clear west permits one possible bat contact versus
three when standing; requiring every fallback move to approach somebody was the
wrong constraint. No increased hold cap or poisoned-Berserk exception was added.

Robin's examined Throw Ally has small conditional direct damage but can relocate
another body; Battlecry is potential Might, not haste or a claimed active buff.
Displayed buffed attack damage is not multiplied twice. Support invalidates
ordinary formation certificates. Separate sole-known low-HP support-book rage
preparation requires at least75% HP, two currently clear visible approach steps,
bounded activation exposure and an aftermath reserve; known companions, missing
staging, control, unresolved foes and restrictive statuses veto it.

Butterflies were another interpretation error: the pinned
`dat/mons/butterfly.yaml` has no attacks despite hostile attitude, flight and
250% speed. They remain visible bodies, not invented fast melee attackers.
Clockroach's native range warding blocks nonadjacent original-source HP damage
but explicitly preserves non-damaging effects. The policy avoids damage-only
ranged spending while retaining roots/corrosion/status darts and adjacent damage.
Its displayed Sandblast is accepted as a bounded pure-damage book, not inherited
physical bite damage or invented hard control.

Brief additional-foe interruption now suspends a bounded shooter approach without
refilling or automatically discarding its original budget. The historical seed5
terminal is **not forced forward**: its real LOS loss, unresolved hobgoblin and
expired clock still disallow continuation.

### Development 17 checked native mechanism evidence

`uv run --locked pytest -q` passed **1,336 tests in59.63s** before these native
checks. The exact28-module policy is frozen at
`runs/native-development17-check/frozen-policy/manifest.json`.
Cold saved-development16 continuations used owned disposable accounts, strict
starting-turn guards and no policy-history restoration or altered save.

| Source seed | Native run | Turns | Advancing decisions | Kills | Final HP / place | Endpoint |
|---|---|---:|---:|---:|---|---|
| 1 | `20261031T182846.916943Z-f2cce7032580` | 4684→4685 | 1 | 0 | 57/57, D:6 | Safety pause |
| 2 | `20261031T182846.915865Z-63743197e70c` | 3398→3558 | 150 | 2 | 57/57, D:5 | 160-turn cap |
| 4 | `20261031T182846.916307Z-ae697dce651c` | 990→1150 | 160 | 5 | 14/39, D:2 | 160-turn cap |
| 6 | `20261031T182846.919148Z-7233b8a2b2b7` | 3321→3322 | 1 | 0 | 45/51, D:4 | Safety pause |

All saved cleanly. Seed2 actually killed the clockroach, recovered, advanced XL
and continued; its70.04s VP8/1280×960 recording played and sought to5s in Chromium
with readyState4/no media error, showing the native kill message. Seed4 executed
two bounded caster closes from the real plain-Slow pause and continued160 turns;
its64.4s VP8/1280×960 recording decoded in the media preview. Its low-HP endpoint
is not called a completed safe run or a resolved future encounter.
Recordings are `gameplay.webm` inside each native run directory.

Anonymous live lobby selection and native gameplay were visually observed for
`Eval15db222235274dbf`, HP26→29/31, XL3/D:2, without authenticating as the bot.
Screenshot: `runs/spectator-video-evidence/20261031/development17-live.png`.
That is Linux Chromium evidence, not a newly exercised Mac/Firefox session.
All parent spectator/playback tabs were closed afterward.

The Jeremiah and Eustachio endpoints remain genuine limitations, not successes
hidden by the two longer checks. Eustachio's first withdrawal used legal visited
northwest ground despite clouded westward interception; Hand must not preempt a
credible retreat just to satisfy an old test. The next observed boundary lost the
bat from LOS and retained an unresolved count. Once-only Hand/interception tests
use explicit synthetic no-exit cases rather than rewriting the native capture.
Incidental exact-move/old-counter/wording assertions were deleted, not repinned.

Fresh development17 was launched from this frozen policy, eight seeds with the
same8000-turn/20000-action/1800-second bounds and concurrency8; recordings for
2,3,4,6 are exploratory evidence, not the final no-recording comparison.

### Final held-out comparison protocol, declared before trace review

The final candidate will be frozen before opening held-out101–130 traces.
Compare30 fresh starts against the same30 baseline seeds, concurrency8,
8000 turns,20000 actions and1800 seconds per worker; no recordings or other
owned native games during that formal batch. D:5 observed attainment is the
initial primary comparison because baseline D:10/Lair attainment is zero;
D:10, Lair and later goals remain reported secondary progression measures.
Wilson intervals and the exact two-sided paired McNemar/binomial test describe
observed attainment under these bounds, **not survival probability**. Report
unadjusted p-values as such; do not call pauses/caps wins or improved survival.
Review all baseline/improved deaths after freeze for the earliest supported
avoidable mistake; unknown postmortem consumables are not available known escapes.
Any later tuning requires new held-out data rather than retesting these seeds.

The paired uncertainty implementation passed37 focused reporting tests in0.23s,
then the complete candidate/analysis suite passed **1,339 tests in59.80s**.
An actual annotated report command over baseline/development16 completed and
returned D:5 discordance1 gain/2 losses, exact two-sided p=1.0; all five baseline
development deaths and the one development16 death retained their reviewed human
annotations. This is report-path smoke evidence, not an improvement claim.

### Stabilized handover

The user stopped the open-ended optimization and approved stabilization/handover
only: no new gameplay features or benchmark batches. The final held-out protocol
above was **not executed**. Thirty baseline held-out games exist, but individual
held-out traces remain sealed/unreviewed; no final improved30 comparison or
supported general survival/win-rate claim exists.

Fresh development17 completed eight valid common-schema-matched starts with no
pairing mismatches. It is an exploratory eight-seed result, not the final
no-recording comparison (four development runs recorded video):

| Observed metric | Baseline development | Development17 |
|---|---:|---:|
| D:5 attainment | 3/8 | 1/8 |
| D:5 Wilson95% interval | 13.68–69.43% | 2.24–47.09% |
| D:10 / Lair attainment | 0/8 / 0/8 | 0/8 / 0/8 |
| Deaths | 5 | 0 |
| Safety-paused runs | 3 | 8 |
| Median / maximum Dungeon depth | 4 / 6 | 3 / 5 |
| Median / maximum XL | 6 / 7 | 4 / 6 |
| Median / maximum observed turns | 2518.5 / 4207 | 2330.5 / 3775 |

Paired D:5 discordance was zero gains/two losses, exact two-sided p=0.5.
Fewer deaths with more pauses is **not demonstrated survival improvement**.
No protocol/action-error events or unchanged-state actions were observed in this
candidate batch. All workers saved normally without forced shutdown.
Full comparison: `.runtime/development17-comparison.json`; native logs,
summaries, frozen source and selected recordings:
`runs/benchmarks/mibe-survival/development17/`.

The active gameplay source was restored to the exact previously checked/native-
exercised28-module snapshot at
`runs/native-development17-check/frozen-policy/manifest.json`. The checked paired
reporting changes remain. Incomplete development18 code, tests, fixtures and the
caster integration draft were preserved at
`.runtime/mibe-development18-set-aside/`, then removed from active source/tests.
Its manifest identifies archived hashes and scope. These local ignored artifacts,
as well as `runs/`, must be preserved separately before deleting this workspace.
No commit/push/PR/merge was performed for this optimization request.

Highest-value unresolved failures:

1. **Unresolved-foe accounting:** ordinary full-HP encounters can stop because a
   previously seen body is no longer visible. Any future graded reserve must
   retain explicit unknown positions/capabilities and price possible incoming
   damage; it must not silently erase real ranged/control threats.
2. **Protected soft casters:** the D:5 wizard pause had51/51HP and a displayed2%
   Confuse chance under Hand, but those odds were cached from the preceding turn.
   The archived proposal adds public inspection freshness and finite ordinary
   closing. It is unintegrated and unverified, not permission to rage into
   invisibility/hard control or treat Will as immunity.
3. **Early duel flapping:** seed8 approached a hobgoblin after the stone-throwing
   kobold left LOS, then resumed retreat when it reappeared, taking pursuit hits
   before finally killing the hobgoblin and pausing at10/19HP. Cached description/
   LOS loss could falsely reset retreat progress. The proposed counter/ordinary-
   duel changes were set aside, not shipped as a tested fix.

Native source also disproved two tempting shortcuts: opportunity pursuers are
not marked only at adjacency (`movement.cc:220–244`, `mon-act.cc:3641–3694`), and
frilled lizards are amphibious (`dat/mons/frilled-lizard.yaml:16`), not ordinary
land movers. Do not weaken reach/AoO accounting or exclude their deep-water routes
to bypass the observed clear-wall encounter.

Existing live controls and pause-aware recording were intentionally unchanged.
The two160-turn native continuation receipts and playable videos above establish
mechanism/runtime behavior only; they do not override the failed fresh progression
comparison or prove later survival. No new native game was launched during the
approved stabilization pass.

Final stabilization verification:

* `uv run --locked pytest -q` — **1,339 passed in 60.15s** after restoration and
  removal of the archived experimental files.
* `uv run --locked dcss-bot --help` — normal command entry point and controls
  loaded successfully.
* A throwaway captured-public-observation smoke exercised the restored clockroach
  path: bounded shooter move `b`, `(27,10) → (26,11)`, visible/walkable/cloud-free
  destination and strictly reduced enemy distance. It opened no game connection
  and sent no gameplay input.
* The active development worker was cancelled. The owned localhost18086 recording
  preview service was stopped; gameplay server/saves were not stopped or modified
  by stabilization. Parent browser tabs had already been closed.

## Bounded routine-play corrections — 2026-10-31

Scope: less eager Berserk, native `5` recovery, native `o` routine exploration,
and explicit routing to spotted scrolls/potions. This is not another survival
optimization cohort or evidence of a better progression/win rate.

Diagnosis and implementation:

* A conservative three-action danger forecast could authorize rage against two
  trivial lizards. Routine commitment now separately requires a difficult fight;
  native tough/nasty classification, substantial public attack/group damage or
  significant observed loss can qualify. Aftermath and urgent-resource guards
  remain. The ordinary-lizard counterexample now chooses one melee action.
* Strategy tracked equipment destinations, not supply destinations. Worse, a
  described non-equipment item could mark its square attempted without pickup.
  The recorded `MiBeBot` scroll at turn518 is preserved as a public fixture.
  `?`/`!` destinations now survive LOS loss/rebases and route before more
  exploration/distant equipment. Unsafe pursuits defer rather than forget.
  Existing RC already enabled scroll/potion autopickup; this was not diagnosed
  as a broken native autopickup toggle.
* Native `5` replaces paced quiet recovery. Poison remains one-turn reassessment.
  Native `o` handles safe reachable-frontier exploration, with explicit public
  completion/no-progress fallback, five-second yields, live pacing and
  acknowledged Escape cancellation. Combat remains one consequential action.
* Native mixed-pile selection validates unique enabled floor hotkeys, acknowledges
  each selection, and commits once. Unknown supplies are not used to collect them.

Verification:

* `uv run --locked pytest -q` — **1,438 passed in65.64s**. Manual-navigation tests
  now explicitly exercise fallback after a synthetic native partial/no-progress
  response; important terrain, failed-move, retreat and stair boundaries remain.
  Obsolete single-turn/default-objective pins were removed, not blindly repinned.
* Fresh disposable seed2 `Smoke0eeb1b5a`:
  `runs/native-routine-smoke/20261031T195907.778624Z-46935e1b2af2`.
  Real authentication/creation,228 game turns,79 decisions,5 kills, pause/resume/
  step and orderly save. Ten native exploration commands and four rest commands
  executed. Cooldown-only rest advanced43→53 in0.256s and cleared `-Berserk`.
  The requested220-turn guard stopped at228 because public updates are granular;
  native turn limits are not exact engine-tick limits.
* That run's `gameplay.webm` decoded in Chromium:26.24s, with12.01s of deliberate
  paused holds excluded. `smoke.json`, actions and events accompany the recording.
* Owned continuation:
  `runs/native-routine-smoke/20261031T200600.607031Z-cca7e10593aa`.
  Another420 turns,9 kills, XL2, saved at lifetime turn648, no policy pause.
  A pause requested during native `o` at turn234 acknowledged at235 in0.964s;
  turn/action counts stayed stable afterward. One paused native exploration step
  advanced13 turns, then stayed paused. The live delay changed from0.4 to0.
  `native-control-smoke.json` preserves the receipt.
* Native auto-explore collected an inky potion at486→504 and a cyan potion at
  513→558; public inventory quantities and native pickup messages prove acquisition.
  The continuation's50.6s VP8 recording was decoded to a preview grid.
* `runs/native-routine-smoke/native-pile-proof.json`: on that owned character,
  normal public commands dropped its two potions and native `pickup_select ab`
  recovered both unchanged quantities. Saved at656. The first throwaway harness
  used the wrong category-navigation key; corrected to native CK_RIGHT before
  the passing proof. No engine item spawning, hidden state or balance changes.

Limits: no scroll was exposed in the bounded live runs; scroll destination,
unknown-scroll menu and mixed-scroll/potion behavior are covered by public-capture
and deterministic regressions, not a fresh live scroll-acquisition claim. These
checks do not measure survival improvement. Public redraws can overshoot macro
turn guards. A native step is one command, not necessarily one game turn.
The valued `MiBeBot`, its save/RC, container and LAN spectator proxy were not
stopped, reauthenticated or changed by these smokes. A final read-only check
found the default control socket absent; its last recorded run ended
`stopped`/`saved` at turn1064, XL2,26/26HP. No automatic resume was performed.
The next supervisor launch loads the new policy; no hot reload was attempted.

## Native exploration follow-through and snake rage — 2026-10-31

Follow-up observations exposed gaps in the preceding smoke's coverage:

* `MiBeBot` run `20261031T201941.362131Z-7521bc787af8`, sequences58–84,
  walked27 consecutive manual turns toward a speculative corner viewpoint
  without revealing terrain. Native `o` was wrongly gated on the custom
  frontier finder. It is now attempted independently when routine travel is
  safe. Fresh “Done exploring” skips custom frontier/corner sweeps and permits
  guarded progression; benign interruptions can resume on known ground.
* At sequence115, turn1204, the character had26/26HP,AC5,EV10, a +2hand axe,
  no statuses or recent damage, and one adder two tiles away. Its public bite
  maximum was4, estimated three-action loss6.03, burst12. Native threat2 alone
  admitted Berserk. That standalone exception is removed. The actual captured
  observation/history is now `recorded_berserk_snake.json`; ordinary defence
  and stronger public-pressure/aftermath boundaries have regression coverage.
* The client deliberately sent Escape every five seconds. Native travel only
  polls `kbhit()` (`travel.cc:1062–1066`, `delay.cc:407–411`), leaving that
  invalid regular-command key queued. Normal exploration no longer has this
  periodic interruption. Explicit paused stepping alone has a short bound.
* Busy/command interruption uses turn-free Ctrl-R18 (`cmd-keys.h:164`,
  `main.cc:2418–2421`); Ctrl-L12 is attack-right and must not be substituted.
  Real menus/prompts retain Escape. The adapter waits for the interrupt's own
  NORMAL/COMMAND acknowledgement, not merely travel's earlier stopping-ready.
  A new queued-key race regression failed before this final acknowledgement fix.
* An old COMMAND snapshot cannot finish a new `o` before its own NORMAL start.
  Already-answered prompt state cannot queue a duplicate cancellation. Genuine
  no-progress/transport/cancellation waits remain bounded and are not renewed
  by pings/repeated messages. Native `set_option` was ruled out as a key source
  by `tileweb.cc:449,607–615,632,644–665`.
* Tightening rage exposed an old lone-Robin freeze: the ordinary fallback
  treated its single visible body as an ally formation. Existing one-step
  damage/control limits now permit ordinary defence; visible/unresolved
  companions retain the formation restriction. The incidental default-rage
  expectation was deleted, while the recorded no-freeze and safety cases remain.

Historical action logs establish periodic five-second cancellations; they did
not record outbound wire order and cannot identify every faster interruption
reported by the observer. The stale-ready/queued-key races are independently
reproduced regressions, not a fabricated reconstruction of the historical wire.

Final verification:

* `uv run --locked pytest -q` — **1,466 passed in74.85s**.
* `uv run --locked --extra video python .runtime/native-explore-wire-smoke.py`
  used fresh seed2 account `ExploreFix1af3354d`; the throwaway harness is removed
  after proof. No valued account was used as a test.
* Receipt directory:
  `runs/native-explore-fix-smoke/20261031T204716.781296Z-5402cfe3e5dd`.
  Actual rule policy,181 turns,59 decisions,5 kills, orderly save.
  A single native `o` ran15 turns over6.300s before an explicit test pause.
  Pause acknowledged in0.674s; paused turn/action counts remained stable.
  Native exploration single-step completed and stayed paused; live pacing
  changed0.4→0. No normal-play `yielded` results occurred.
* **Zero “Unknown command” messages** across the run. Exactly one redraw
  interruption was sent, for the explicit pause—not a periodic exploration loop.
  `native-wire.jsonl` records sanitized outgoing keys/options and public
  progress/input modes; `native-smoke.json` records the assertions.
* The native spectator recording `gameplay.webm` decoded to a six-frame preview:
  VP8,1280×960,25fps,42.8s. Actions/events/observations accompany it.

These are protocol/control and policy-regression checks, not survival benchmarks.
The weak-snake correction is exercised against the actual recorded observation,
not a claim of a fresh live adder encounter in this short smoke.
The user's run had already stopped/saved at turn1739,D:2,31/31HP when the pause
request found no default control socket. No MiBeBot reauthentication, automatic
resume, container restart or LAN-proxy change occurred. Load the corrected
policy on the next supervisor launch; source changes do not hot-reload a process.


## Independent native pacing and map travel — 2026-10-31

The observer's continued tiny-step report exposed a separate pacing defect.
Run `20261031T205202.387578Z-94f8bd489254` recorded one native `o` advancing
17 turns in7.091s: the0.4s decision delay was also applied to each native tile.
Removing periodic cancellation did not fix that coupling.

Decision pacing remains0.4s by default. Independent `EXPLORE_DELAY` /
`--explore-delay` / live `explore-delay` defaults to0.02s per native tile and is
validated against the engine's0–2s range. Decision-speed changes do not interrupt
travel; native-speed changes do, using the existing consumed-key boundary.

After safe exploration completion, the policy selects an eligible ordinary
downstairs using existing danger/route exclusions, then uses native map travel.
The adapter sends `X`, verifies individually acknowledged `>` selections against
the exact public staircase coordinate, and commits Enter. Hatches/branches are
not accepted substitutes. Arrival stops on the same floor; descent is a separate
fresh decision. Real tactical hazards retain manual routing.

Real verification used fresh disposable seed2 account `PaceFixe695f8f8`, not
`MiBeBot`, against the pinned0.34.1 server:

* `uv run --locked pytest -q` — **1,542 passed in91.27s**.
* `uv run --locked --extra video python .runtime/native-pacing-stairs-smoke.py`
  exercised181 actual game turns, independent live pacing controls, stable pause,
  orderly save, reconnect, native map travel and anonymous video recording.
  The owned throwaway harness was removed after proof.
* Receipt and logs:
  `runs/native-pacing-stairs-smoke/20261031T211423.241480Z-15249677a62b`.
  `pacing-stairs-smoke.json` records five native exploration samples spanning
  13–38 turns at0.027–0.035 seconds per turn, including38 turns in1.030s.
  Changing decision delay during `o` did not interrupt it. The run produced no
  fresh “Unknown command” messages.
* Actual map travel sent keycodes88,62,13 (`X`, `>`, Enter), moved three game
  turns from(0,0) to the selected downstairs(2,-1), and stayed on Dungeon:1.
  The owned character saved normally afterwards. This specifically proves
  transport/arrival, not a complete new floor-clear progression benchmark.
* `stair-travel/gameplay.webm` decoded and was visually inspected: VP8,
  1280×960,25fps,1.2s; native map display followed by arrival on the staircase.
  The main run's recording and structured observations/actions are also retained.

These checks establish pacing and native travel behavior, not improved survival
or new branch-progression results.

Live deployment preserved the already safety-paused `MiBeBot` rather than
forcing the unresolved encounter. The old run acknowledged `save: saved`;
the replacement persistent supervisor is
`runs/20261031T211858.679613Z-be21fc5b8d96`, paused with zero actions at the same
turn2406,Dungeon:3,39/39HP,XL4. Remaining invocation budgets are9702 actions /
9333 turns, with recording enabled, decision delay0.4 and native delay0.02.
The prior run and its tactical history remain on disk; in-memory policy history
is not restored by a process restart. Loading this update does not resolve the
encounter that caused the earlier safety pause. Neither the server nor LAN
forward was restarted; the existing private LAN WebTiles endpoint responded.
