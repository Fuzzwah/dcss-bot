# Architecture and integration decision

## Research (2026-10-30)

* [Official DCSS](https://github.com/crawl/crawl): active upstream; latest published
  release observed was [0.34.1](https://github.com/crawl/crawl/releases/tag/0.34.1),
  commit `1eebc1a2892e1c89776a0d7a10691f8dac8d9796`. The game is
  [GPL-2.0-or-later](https://github.com/crawl/crawl/blob/0.34.1/LICENSE), with
  separately documented compatible component/art licences. We build the unchanged
  engine and native Python/Tornado WebTiles server, retaining upstream source and licence.
* [nkhoit/dcss-ai](https://github.com/nkhoit/dcss-ai): Python WebSocket client,
  high-level game tools, Copilot SDK decision loop and streaming overlay. Repository
  API reports last push 2026-02-17; no licence file in the root and no declared licence.
  Its [Compose file](https://github.com/nkhoit/dcss-ai/blob/main/server/docker-compose.yml)
  tracks `ghcr.io/nkhoit/dcss-webtiles:latest`, so a precise supported engine version
  is not established. **No code copied or imported.** Public source is not permission
  to reuse it. Paid Copilot is also unsuitable for the default deployment.
* [dtdannen/dcss-ai-wrapper](https://github.com/dtdannen/dcss-ai-wrapper):
  [MIT](https://github.com/dtdannen/dcss-ai-wrapper/blob/master/LICENSE), last push
  reported 2026-08-17. Its [WebSocket implementation](https://github.com/dtdannen/dcss-ai-wrapper/blob/master/src/dcss/connection/autobahn_game_connection.py)
  uses Autobahn, a stateful raw-deflate decoder, menu parsing and an embedded agent
  loop. [Configuration](https://github.com/dtdannen/dcss-ai-wrapper/blob/master/src/dcss/connection/config.py)
  defaults to `tut-web-trunk`, contains older local Unix socket paths and does not
  claim tested compatibility with 0.34.1. Symbolic/vector state and research examples
  are useful future references. MIT permits adaptation with attribution, but adopting
  its broader stack would add dependencies and version migration work.

Decision: an original minimal Python client using the official WebTiles protocol.
No engine patches, no local save inspection, no wizard mode. Maintenance cost is
explicitly tied to a pinned version rather than pretending the wire API is stable.

Protocol references: upstream [ws_handler.py](https://github.com/crawl/crawl/blob/0.34.1/crawl-ref/source/webserver/webtiles/ws_handler.py),
[tileweb.cc](https://github.com/crawl/crawl/blob/0.34.1/crawl-ref/source/tileweb.cc), and
[browser map decoder](https://github.com/crawl/crawl/blob/0.34.1/crawl-ref/source/webserver/game_data/static/map_knowledge.js).

## Boundaries

* `models.py`: immutable observations, bounded validated actions, async `Agent` protocol.
* `state.py`: player/map/menu deltas; monster identity changes; visibility filtering.
* `characters/base.py`: character setup/identity contract and strategy factory.
* `characters/mibe.py`: MiBe profile, rule-v3 policy and equipment preferences.
* `characters/__init__.py`: explicit built-in registry and installed profile loading.
* `agents.py`: decision-engine selection; `rule` uses the selected profile's factory.
* `danger.py`: shared public-state risk annotations, not character equipment/tactics.
* `adapter.py`: connection, local registration, verified profile RC setup, prompts,
  key dispatch and reconnect; character choices are delegated to the profile.
* Strategy: sees only `Observation`, returns `Action` with reason/objective.
* Supervisor: single-flight actions, controls, pacing, timeouts and loop limits.
* Recorder: per-run JSONL decisions/observations/events and final metrics; optional
  independent anonymous spectator browser for WebM capture.

Spectators use the real upstream renderer on their own anonymous WebSocket. They do
not log into the agent account and cannot send gameplay commands to it.

### Pause-aware capture

Playwright's public video API has no pause/resume operation. `VideoRecorder`
therefore opens a recording context only during active execution and finalizes
that segment at each pause. The supervisor serializes recording transitions with
game actions: pause acknowledgement follows segment closure, resume opens capture
before further actions, and a step records exactly its active interval before
returning to pause. Automatic safety/error/reconnect pauses use the same boundary.
An initially paused run does not launch the capture browser or create video.

On orderly shutdown, one segment is moved directly to `gameplay.webm`; multiple
segments are joined with system FFmpeg's concat demuxer and stream-copy. Fixed
relative segment names avoid interpolating paths or URLs into FFmpeg syntax.
Successful finalization removes temporary segments; failed capture/join preserves
recoverable files. A never-active run returns no video, not an empty placeholder.
Capture errors disable video but leave gameplay and JSONL recording operational.
JSONL timestamps stay wall-clock accurate; video time excludes pause gaps.


## Safety and protocol invariants

* Request `no-compression`; support upstream stateful raw DEFLATE if a server chooses
  compressed frames. Reinitialize decompression and state on reconnect.
* Merge sparse player/inventory/cell updates, including implicit adjacent x coordinates
  and monsters moving by identity. Clear level knowledge on `map.clear`.
* Only currently visible hostiles are threat observations. Remembered terrain is
  permitted, but remembered monsters are not presented as still visible.
* Raw inventory subtype/plus values may reveal unidentified items. They are excluded
  from the agent interface and decision logs; only displayed names are exposed.
* Worn armour is absent from the sparse `player.inv` names. Read it from the native
  inventory menu at character readiness and after wearing equipment; match visible
  names and hotkeys back to inventory slots because supply/equipment letters overlap.
  Never infer successful wearing merely because a command was sent.
* One action in flight. After a lost connection, reattach and observe rather than
  replay an ambiguous command. Unknown prompts pause; unsolicited yes/no prompts
  are declined. Stair travel with visible hostiles is rejected.
  After a key, native input mode 0 is busy/rendering, not a command boundary.
  Wait within the existing timeout for a command, prompt, menu or terminal state;
  level generation can exceed the 120ms packet-coalescing gap. Never replay the key.
* Native level-up prompts embed hotkeys inside words, such as `(S)trength`.
  The selected profile normalizes decoration for recognition and chooses its stat's
  uppercase hotkey. MiBe chooses `S`. Never answer stale prompt text during play.
* Logs are observational replay, not a promise of exact engine re-simulation.
  Version pinning and saved event history enable future controlled comparisons.
* Ordinary action cycles remain bounded. One-turn recovery with visible temporary
  statuses may wait for a hidden timer, but stops after 120 turns without visible
  improvement. Cooldown-only `-Berserk` is not a reason to rest.
  Progress comparisons ignore regenerated monster IDs, while retaining visible
  names, positions, threat and attitude. Transport identity alone is not progress.

`rule-v3` explores known walkable frontier cells adjacent to unknown terrain.
It excludes diagonal unknowns sealed behind two known nonwalkable sides and
uses ordinary safe pathfinding to reject unreachable frontiers. A viewpoint is
retired only after occupying it with its current local unknown-neighbor signature;
approaching it does not count as exploration, and changed knowledge can reactivate
it. State resets between locations. This is a boundary heuristic, not a LOS model:
an unproductive viewpoint can still be tried once per changed signature. Healthy,
clear descent follows exhaustion of useful reachable frontiers, not a requirement
to visit every floor square.
If an otherwise reachable frontier is blocked only by clouds, wait on safe ground
under the supervisor's stall bound rather than crossing the hazard or treating
the floor as explored.

Ranged luring remembers an encounter across short LOS gaps and ID changes. After
twelve turns, a healthy agent may close on a lone ordinary shooter; dangerous
shooters, injury and statuses retain safety priority. An adjacent foe following
a retreat marks distance-only escape as ineffective. Further movement must improve
cover or reduce the formation even if one foe follows; healing, existing berserk
conditions and single-step defensive attacks remain available. Neither heuristic
assumes hidden monster speed, actual future movement or a safe staircase.

Visible reach-attack messages permit closing a safe step on the attacker; ordinary
foes are not assumed to carry hidden polearms. An already-berserk agent can close
on ordinary visible enemies instead of waiting out its duration, without stepping
into multiple adjacent foes. Liquid fire takes movement priority after available
healing: rank clear visible squares by adjacent attackers, exposure and distance.
Doors/clouds are not valid extinguishing steps. If no safe movement exists, pause
instead of pretending a stationary wait is recovery.

## Character modules

`CharacterProfile` contains setup preferences and an `agent_factory`, not transport
state. A run resolves `BOT_CHARACTER`/`--character` once and passes the profile to
the adapter and decision-engine factory. Each default policy instance has its own
mutable encounter/pathfinding state. `--agent module:Class` retains the independent
zero-argument async decision-engine contract.

MiBe is the only built-in module. Other installed modules expose `module:PROFILE`;
optional short aliases belong in the explicit registry. There is no dynamic scan,
species/background cross-product or nominal support for unimplemented policies.
The profile owns creation/stat choices, RC preferences and identity hooks; the
module's strategy owns equipment and tactical decisions. Unusual UI can override
profile methods without adding character branches to the adapter. New generic
game mechanics may still need new validated actions.

Before initial play, authenticated `get_rc`/`set_rc`/exact `get_rc` readback replaces
profile-owned assignments, including legacy species/background/weapon defaults.
Unrelated settings are preserved. Seed setup uses the same transaction, retaining
the fresh-account guard; reconnect never rewrites RC. The server's `default.rc`
now contains only generic UI/safety defaults.

Identity comes only from player-visible messages. Fresh native welcomes name the
starting species/background; player packets expose species and current god, not
original background. Saved lobby links use native `short_desc(false)`:
`[Name, a level N Species Background of God]`. The adapter extracts the description
and delegates combo matching to the profile. `[playing]` provides no background
proof, so attachment checks only the available public identity. No save files are
queried by the bot, and no saved character is converted to the selected profile.

## Strategy extension and LLM boundary

The working strategy is `characters.mibe.RuleBasedAgent`. `--character` selects
setup and its default policy; `--agent module:Class` instead loads an installed
decision engine with a no-argument constructor and `async decide`.
The supervisor revalidates every returned `Action`, services UI housekeeping
before strategy calls, bounds decision latency, and pauses/logs exceptions or
invalid output. An independent strategy was exercised against the real game
without modifying the adapter.

An LLM provider integration is **not included**. A provider plugin should own its
endpoint URL, model identifier, optional API key, request timeout, and usage/cost
accounting—not the game adapter. OpenAI-compatible local servers and OpenRouter
can use the same chat-completions request contract with different base URLs and
headers. Convert a strict JSON object to `Action`; never execute model-generated
code or forward arbitrary key strings. Returning `Action("pause", reason=...)`
or raising a bounded exception gives a clean logged pause on provider failure;
a hybrid plugin can instead explicitly delegate to `RuleBasedAgent`.

## Verified edge cases and deliberate limits

Real runs exposed two map/UI details covered by regressions: harmless flora uses
hostile wire attitude, and re-observed monsters can receive a new client ID while
inheriting unchanged fields from the existing cell. Modal confirmations take
their prompt from the menu title, not the last dungeon message.

The policy uses game-visible names and threat classifications, not save files or
unidentified numeric item properties. Passive vegetation is not an enemy;
trivial rats, oklob plants and active ballistomycetes remain threats. Stair
destinations are not assumed safe. Unknown actions/prompts pause rather than
invent a fallback key sequence.

Video and JSONL are usable replay/evaluation artifacts, but there is no exact
deterministic re-simulation, long-term learning, LLM token accounting, or
multi-agent tournament service.

## Seeded evaluation boundary

`evaluation.py` runs sequential fresh-account trials through the same adapter,
supervisor and recorder as watchable play. It owns batch orchestration only:
strategy state is recreated for each trial, and the normal bot's socket and
account are never reused. Strategies receive ordinary player-visible observations,
including the native seed banner, but no save-file internals or hidden map state.

The pinned engine's supported `game_seed` rc option is set before `play`, with
`pregen_dungeon = incremental` (full pregeneration is not supported by the
DGAMELAUNCH build). `set_rc` has no acknowledgement, so the adapter follows it
with an exact `get_rc` readback. An existing authenticated account is rejected
before any rc write or play request. Seeded reconnects require an advertised
saved character and wait past client/layout packets for actual player state;
they never confirm creation dialogues or create a replacement after death.
The server exposes a dedicated `dcss-seeded` entry using upstream `-seed`. Its
creation dialogue accepts the prefilled rc seed. `CRAWL_DIR` also points the
upstream save-inspection helper at the persistent game directory, so the seeded
lobby entry can advertise and reconnect the correct saved character.

Configuration acceptance is not engine proof. The evaluator separately requires
the public “Game seed: N (custom seed)” startup banner, a turn-zero character
matching the selected profile's species/background/religion, and native reported
release 0.34.1. The full commit pin is deployment provenance, not a cryptographic
attestation from the remote protocol. Two fresh
runs can then be checked for matching public starting state and action/state
traces; account names, protocol monster IDs and console prose are excluded from
those fingerprints, but visible terrain, monsters, inventory, HP and timing in
game turns remain.

Unattended safety pauses terminate/save a trial instead of hanging a batch.
Wall-clock timeouts request bounded supervisor shutdown; the evaluation result
labels them separately from ordinary stops. Graceful Ctrl-C or a stop on the
batch's control socket cancels remaining trials. Each completed result is flushed
before the next trial; partial/interrupted batches retain inspectable reports.

Reported survival turns are right-censored by limits, and pauses/errors are not
successes. Paired comparisons require matching public starting-state and rc
hashes. Small samples, differing action paths and unobserved engine state prevent
claiming general reproducibility or statistical superiority from these checks.
Character metadata, the requested/canonical module, module source hash and recursive
package hashes are retained with results. Aggregation rejects mixed known profiles
or known/unknown identity; historical unknown-profile rows keep descriptive metrics
without asserting comparable starts or repeatability.

## Exploration evidence

Evaluation streams existing public action logs into per-location coordinate sets
and counters, without retaining full map histories in memory. It records observed
arrival turns, completed downward transitions, cumulative known walkable terrain,
visited endpoints, and objective action/turn costs. A movement revisit requires
an actual position-changing `move`/`explore` within the same location; bump attacks,
waiting and stairs do not inflate that counter. Branch and depth disambiguate
coordinates. Intermediate positions of multi-turn commands are not inferred.

Reveal counts refer to previously unknown public walkable cells, not the size of
the hidden level. Missing maps are distinguished from genuine no-reveal actions.
Costs belong to the starting floor; discovered terrain belongs to its observed
floor. Aggregate distributions include valid-start deaths, pauses and time limits,
with reached-floor sample counts and unobserved-floor outcomes alongside conditional
arrival means. Historical reports without these metrics have a separate missing
denominator. These measurements complement, rather than replace, survival outcomes.
