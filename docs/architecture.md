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
* `characters/mibe.py`: MiBe profile and rule-v4 orchestration; separate tactical,
  pinned-mechanics, equipment, development, progression and exploration modules.
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
* Raw inventory subtype/plus/identification flags may reveal unidentified items.
  They are excluded from agent observations. Visible `base_type` class and rendered
  names are exposed. `item_names.py` reconciles terse inventory and verbose menu
  labels without erasing known brands or ordinary armour egos.
* Worn armour is absent from the sparse `player.inv` names. Read it from the native
  inventory menu at character readiness and after wearing equipment; match visible
  names and hotkeys back to inventory slots because supply/equipment letters overlap.
  Never infer successful wearing merely because a command was sent.
  Global identification can rename supplies without a changed-name packet.
  Inventory refresh visits every nonempty native category page (Gear, Potions,
  Scrolls, Evocable Items) and reconciles unique visible letter/class/quantity
  mappings. Category updates truncate old menu rows to the native item count.
  Known Identify preflights that refresh and returns changed identities for
  reconsideration before spending a scroll. Actual source/target dispatch still
  validates the native visible name, class, quantity and enabled unique hotkey.
* Item development is delegated to strategy only for native `use_item` with the
  exact Identify, Enchant weapon or Enchant item title.
  Both explicit identify actions and follow-on menu selections must name one
  selectable current-menu hotkey, unique across every row; inventory letters
  alone cannot disambiguate equipment and supplies. Other item menus are not
  authorization to guess a key.
* Ability dispatch reopens the native ability menu before selecting by displayed
  semantic name. If a formerly cached ability is now absent or unavailable,
  close that expected menu and return its refreshed public availability to
  strategy without casting. Unexpected interfaces and ambiguous names fail closed.
* Monster descriptions expose visible weapon columns from native `Hit:`/`Shoot:`
  attack-table rows as well as explicit carrying/wielding/wearing prose. No raw
  monster equipment or unidentified item internals enter those fields.
  Displayed `range_string` labels become `Monster.spell_ranges`; hidden spell
  power/HD are not exposed. Contact-only Freeze is not a ranged attack. The
  ordinary effective LOS fallback is seven; verified short ranges and current
  public labels take precedence. Displayed dice/ranges inform damage estimates.
* Bounded `attack` accepts one direction and sends native Ctrl-direction
  (`CMD_ATTACK_*`). With a melee weapon it swings without walking, including at
  empty/unseen squares. It is not an autofight macro or arbitrary key injection.
* One action in flight. After a lost connection, reattach and observe rather than
  replay an ambiguous command. Unknown prompts pause; unsolicited yes/no prompts
  are declined. Routine stair travel with visible hostiles is rejected.
  `escape_upstairs` is a distinct bounded action: ordinary upstairs underfoot
  and no visible adjacent hostile. The strategy, not transport, scores its risk.
  After a key, native input mode 0 is busy/rendering, not a command boundary.
  Wait within the existing timeout for a command, prompt, menu or terminal state;
  level generation can exceed the 120ms packet-coalescing gap. Never replay the key.
* Native level-up prompts embed hotkeys inside words, such as `(S)trength`.
  The selected profile normalizes decoration for recognition and chooses its stat's
  uppercase hotkey. MiBe chooses `S`. Never answer stale prompt text during play.
* Logs are observational replay, not a promise of exact engine re-simulation.
  Version pinning and saved event history enable future controlled comparisons.
* Ordinary action cycles remain bounded. Advancing recovery with visible temporary
  statuses stops after 120 turns without visible improvement. Quiet cooldown-only
  `-Berserk` recovery has a separate default 80-player-turn policy budget:
  readiness precedes exploration, threats preempt recovery, and stale status pauses.
  Progress comparisons ignore regenerated monster IDs, while retaining visible
  names, positions, threat and attitude. Transport identity alone is not progress.

`rule-v4` prefers native `o` for routine safe exploration independently of the
fallback frontier finder. Fresh native completion retires speculative frontier/
corner work for that location/map-knowledge/XL signature and permits safe
progression. Benign interrupted commands with public progress resume even on an
unchanged map; explicit control cancellation can resume without progress.
Partial/no-progress commands retain guarded manual fallback. After ordinary
frontiers are exhausted, bounded unseen-diagonal probes can reveal native squeezes.
A viewpoint is retired only after occupying it with its current local unknown-neighbor signature;
approaching it does not count as exploration, and changed knowledge can reactivate
it. `mibe_exploration.py` owns typed per-location visits, failed moves, exhausted
frontier signatures, pickup attempts, equipment and scroll/potion destinations.
Native map coordinates are relative to a WebTiles origin, not stable world coordinates:
`tileweb.cc` resets that origin in `clear_minimap()` and reports both player and
terrain relative to it. Returning floors and observable same-floor map resets
therefore restore position memory only after matching unique public ordinary
stair, D:1 exit or Lair entrance/exit feature variants; every common anchor must
agree on one translation. Frontier signature values move with their keys.
Absent, duplicated or conflicting anchors invalidate the old position caches;
neither the previous player position nor an uncertain in-flight move can establish
the offset. A newly seen floor starts empty, and ordinary accumulating map updates
need no pathfinding or translation work. Without a frame marker in `Observation`,
a same-floor reset that changes no observable map coordinates is indistinguishable
from an ordinary update; it cannot justify inventing a player-based translation.
This is a boundary heuristic, not a LOS model: an unproductive viewpoint can still
be tried once per changed signature. Healthy, clear descent follows exhaustion of
useful reachable frontiers, not a requirement to visit every floor square.
If an otherwise reachable frontier is blocked only by clouds, wait on safe ground
under the supervisor's stall bound rather than crossing the hazard or treating
the floor as explored.

## Tactical and development policy

`mibe_tactics.py` creates a typed assessment and meaningful candidate list before
each consequential action. `mibe_mechanics.py` keeps 0.34.1 facts separate from
policy thresholds: public monster attack tables/speeds/spells augment a small
catalogue; unknown mechanics remain uncertain rather than importing private stats.
The short-horizon expected/burst model is deliberately conservative, not an exact
damage/LOS/path simulator. Cover claims are refuted when the supposedly hidden
shooter remains visible. Actual failed retreats bound repeated pursuit exposure.
Physical melee rows may share a round, but shooting/throwing and melee are
alternatives. Ranged damage is separate from the close-combat envelope; displayed
spell dice/intervals supply mean and maximum, while unmodelled formulas retain
the conservative allowance. Summoning and support do not become direct beams.
The physical mean uses the pinned core's rolled damage, not the displayed maximum
on every hit. Public rows bound a conservative mean approximation; missing tables
keep the uncertainty bound. Brand proc frequency, exact AC/EV and monster energy
remain approximations. Burst maxima are separate and are not halved.
Displayed projectile and distant-spell maxima do not inflate physical melee rows.
Contact damage spells remain in the close envelope. Pinned `mon-act.cc:1507–1523`
rejects ordinary adjacent shooting; the known ordinary thrower model uses minimum
range two, while ranged specialists, armed-launcher exceptions and unknown types
retain their conservative adjacent capability.
Ensnare is separate entangling control: the pinned beam creates a web and has no
direct damage. Will protection does not remove its movement risk. A completely
displayed control-only ranged book does not inherit the creature's physical bite
as a damaging beam.


`mibe_retreat.py` searches known upstairs/apparent cover anchors and forecasts
successive movement, including enemy energy spent closing. Only visible next
steps are executable; a remembered continuation requires visited terrain and
explicit uncertainty. The controller pins the goal, verifies decreasing route
distance, rebases it with public anchors, and retires failed movement. If a
multi-action forecast rejects every route, a bounded one-action forecast can
authorize the next step; the main multi-action danger assessment remains logged.
This does not certify the unscored remainder, stair traversal or destination.
An unseen remembered threat stays explicitly uncertain rather than becoming
an invented monster coordinate.
Cover is a waypoint toward known upstairs, not a completed escape. Missing
description data on a new native monster ID triggers inspection without retiring
a progressing goal. Native invisible-attack messages and fresh unexplained harm
add uncertain threat even when weak visible enemies remain. The planned next
step excludes a fresh last-seen possible occupied square; no invisible coordinate
is manufactured, and free UI activity cannot renew old incoming damage.
The moving prefix normally excludes reachable melee contact: native pursuers can
approach then make opportunity attacks without a normal attack's energy. At most
one fully examined plain low-damage melee interceptor per forecast step can instead
be damage-budgeted, charging possible opportunity attacks. Strong, reaching,
poison, control and other nonplain interceptors remain excluded. Generic
repositioning compares equal movement horizons and does not call a new melee
interception improved cover. Nets/webs instead choose one native movement-key
struggle; no displacement, attack, or failed-route progress is claimed.
Distant faster pursuers may pass the same per-step interception envelope; faster
speed alone no longer rejects a currently well-separated route.
`mibe_geometry.py` caches finite walking-distance fields per observation. Only
examined mundane land movers use known walls/deep water/lava to bound physical
arrival; unknown terrain remains passable and actor/door costs are ignored.
Reach-two bounds optimistically include walking to attack squares across barriers;
unknown movement, flight and teleporting retain conservative geometric bounds.
Save34 terrain IDs are lava30/deep water31 (land barriers) and shallow water32
(passable). Ranged exposure remains independent: `Threat.ranged` reflects positive
exposure over the forecast horizon, not a static current-distance check. This is
a lower bound on walking time, not an assertion of permanent inaccessibility.

Resource candidates include curing/healing, targeted blink, delayed teleport,
fear/fog, preparation potions, missiles/wands and semantic Trog abilities. Native
menus determine current ability availability and hotkeys. An active teleport is
not restarted; Berserk restrictions and its aftermath are explicit. Axe attacks
consider forbidden adjacent cleave victims, not just the selected bump target.
Might/Resist preparation does not drain stacks while the buff remains active.
`inspect abilities` is a bounded, turn-free native menu read permitted during
combat; full equipment/skill inspection remains clear-ground-only. Missing
availability, piety changes, observed public-data loss and native `-Berserk`/`CD`
recovery refresh it before planning. Native temporary restrictions such as `Conf`
defer the read and force a refresh after recovery, including a cold resume whose
initial menu was greyed out. An unchanged failed/empty response cannot trigger an
infinite free-menu loop.
Routine actions observe the snapshot before replacing prior state; identity-based idempotence
prevents double processing, and damage is retained only for its actual world
turn across UI updates. Assessment evidence is never borrowed from an older
snapshot.
`mibe_unseen.py` bounds native nonmoving searches to untried clear adjacent
directions after fresh public melee evidence. A fresh player-on-“something”
response permits one retry of that successful command; menus, reused text and
empty swings do not. An axe can cleave an unseen secondary target even when its
commanded square is empty (`fight.cc:1128–1146`), so feedback does not establish a
monster coordinate, identity or refreshed last-seen position. Native unseen death
XP stops continuation without naming its victim. Named kills never erase fresh
incoming evidence from another hidden attacker.
Plain movement failing to displace with fresh anonymous outgoing melee is tracked
separately as uncertain blockage evidence. It does not prove invisible identity or
position and does not become a deliberate native Ctrl-direction attack.

A near caster commitment is restricted by the displayed approximate maximum HP,
the Dart/Slow/Call Imp/Throw Flame spell family, current damage/movement bounds, and at most
one weak companion. It does not extend to blink, invisibility or hard control.
A last-resort ordinary duel can use rage below the routine HP threshold only
without another credible resource/escape and with a buffer for one estimated
maximum strike; this is explicitly risky, not a guarantee. All ordinary emergency
backstep paths respect an active rage commitment.
Known single-front queues and small fully visible waves have separate commitment
bounds, including current contacts, visible HP estimates and a reserve for at
most one weak future poison source. Ongoing poison still blocks commitment.
Safe commitment is not sufficient to justify routine rage. A separate difficulty
gate requires a public attack maximum of at least 40% current HP, a group melee
envelope of at least 50%, or recent damage of at least 20% maximum HP. Native
relative threat labels alone do not qualify. These are policy bounds, not hit
predictions. A lone support caster can receive the existing bounded one-step
defence instead of requiring rage; unresolved or visible companions still guard
formation changes.

Prepared Will can be established by the native Hand light without inventing a
missing numeric Will snapshot. The finite small-book fallback is narrower than a
general prepared-caster exception: an examined low-HP Dart/Slow/Call Imp/Throw
Flame book, at most one examined tiny weak companion, and explicit HP/exposure
reserves. Plain Slow costs 1.5 world actions; raw projectile mean is retained
without an evasion discount, and a nonlethal maximum plus aftermath reserve is
required. Failed movement/native-ID churn cannot renew the initial
distance-minus-one close allowance. One affordable minor-only-summoner Hand
attempt and one prepared weak-summon interception are separately bounded;
credible withdrawal outranks last-resort preparation. No general permission for
blink pursuit, invisibility, greater summoning or hard disabling follows.
`mibe_approach.py` separately bounds closing on one examined ordinary shooter with
complete displayed plain projectile or annotated pure-damage spell data, public
HP/speed/damage bounds and no unresolved foes or prohibited statuses/mechanics.
It starts at 80% HP, allows at most six closing actions/eight turns per encounter,
and requires a visible clear path. Remaining expected loss and the next-position
single-attack maximum must each fit current HP with a 15% max-HP reserve.
Each action requires a fresh observation. Brief additional/unresolved foes suspend
execution without refilling the retained original allowance; its clock still
runs. Failed displacement, absent closing progress, actual target LOS loss,
unsafe changed mechanics or spent action/time/HP admission cancel it.
IDs intentionally reset outside seen cells (`player-notices.cc:551–557`), so
stale aliases prove neither another body nor persistent identity. A sole
same-name visible candidate retains the encounter's original action/time budget;
real observed unresolved counts still gate admission. Missing descriptions await
free reexamination, and proven coordinate rebases translate stored positions
without refunding budgets. Route cells must be visible, clear and unoccupied;
an unseen neighboring cell alone is not a forbidden destination, and known open
doors need no opening action. Absolute bursts and a 1.10 expected-loss stress
heuristic are logged separately, not survival percentiles or kill guarantees.
A movement receipt is consumed once. A later attack, resource action or retreat
cannot be mistaken for another result of that old move. A still-admitted active
approach takes precedence over optional return positioning; admitted manageable
shooters do not independently trigger floor evacuation. Fresh safety/resource
overrides and all encounter budgets remain in force. Examined ordinary
goblin/hobgoblin/kobold throwers cannot shoot at adjacency under the pinned native
rule; specialists, Armed ranged-weapon buffs and damaging spells retain exceptions.

Formation safety includes optimistic one-reaction walking arrivals, using public
movement speed/reach and known barriers. The ordinary fallback cannot advance or
hold into more than two possible fronts. When standing admits several bodies,
one stricter poison-reserved step may reduce them to at most one without claiming
an escape route or freedom from webs. Hard control, support relocation and
unresolved foes invalidate that certificate. Existing stationary-wait progress
bounds remain; elapsed time does not renew them.

Ally Throw and Battlecry are explicit support capabilities, not guessed destinations,
new bodies, haste or proof of an active Might buff. Robin's pinned conditional
0–3 direct throw damage is separate from the ally's relocation and mace damage;
other unknown throwers do not inherit that small bound. A narrow sole-known
low-HP support-book preparation may rage at three tiles only with a visible
two-step approach, at least 75% HP and activation/aftermath reserves. Known
companions, unresolved threats and restrictive statuses veto it.

Native butterflies have no attacks; they remain in the visible body list but not
the damaging-hostile projection. Clockroach's public/innate range warding means
nonadjacent original-source HP damage is ineffective, not that roots, corrosion
or status darts are illegal. Damage-only wand/missile choices are filtered without
removing adjacent-origin damage or those non-damaging control candidates.

High-risk melee preferences are distinct from hard harmful melee interactions.
Already-Berserk continuation against an examined ordinary caster does not grant
pre-Berserk permission or override hydra/head-growth, escalating-slime,
dangerous-weapon, poison/control/summoning, restraint or axe-cleave protections.
The native self-enchantment “Berserker Rage” is classified as a self spell rather
than fictitious ranged damage. These are public-mechanics heuristics, not a
promise that the caster will die before rage expires.


Eustachio's minor summoning remains an escalating risk, not a hydra-like
prohibition on axe damage. Existing defended waiting can peel minor summons
under health/exposure and action-budget bounds. Unknown greater summoning does
not inherit that permission.
`EncounterProgress` bounds stationary luring by three unproductive world waits.
Actual approach or movement can restart the budget; a bat circling at the same
distance, elapsed time or free inspection cannot replenish it.

`EnclosureMemory` permits a narrow bypass only after an examined mundane humanoid
undead's entire sealed plain-floor pocket and wall boundary were actually seen.
Continuous occupant identity/capabilities and unchanged known terrain can retain
that historical evidence when a wall leaves LOS. Contradictions, disappearance,
other unsealed threats or floor/frame ambiguity discard it. Historical walls are
not asserted currently visible or guaranteed unchanged. The next route step must
still be visible and clear; neither monsters nor visibility flags are rewritten.
A separate finite-barrier bypass covers partially observed pockets without
claiming enclosure. Every foe must be an examined ordinary land melee mover or
a narrowly certified ordinary physical net-only thrower; optimistic walking
distance must exceed both the four-action contact window and straight-line
distance at the current and next positions. Net-only eligibility does not erase
its independent restraint capability. Every optimistic shooter origin must be
out of physical net range (upper bound eight) or have its path cut by known solid
walls, separately for one departure action at the current endpoint (including
failed displacement) and four actions at the next endpoint. Unknown cells and
water stay projectile-open; actors and door costs are ignored. Only one currently
visible dry, nonapproaching step toward a real frontier/stair is sent. Unknown
cells remain unknown/passable and every foe remains in the next observation.
Neither finite certificate implies permanent enclosure or authorizes later steps.

`mibe_development.py` handles clear-ground public inspections, exclusive skill
targets, bounded unknown-scroll probes and native identification/enchantment
choices. Equipment comparison uses skill-dependent axe delay, brands, shields
and defensive slots. Unrankable artefacts are refused; jewellery fills empty
slots only. Resistance/Will snapshots are invalidated when their public context
(equipment, level or statuses) changes.
Remembered scrolls/potions route before distant equipment and further exploration.
Public remembered item glyphs also survive reconnect or native travel beyond their
original FOV. Only the next step must be visible and clear; unsafe destinations
remain remembered. Underfoot native pile selection uses unique enabled floor-menu
keys, not overlapping bag letters, and acknowledges each multi-selection before
one Enter. No unknown supply is used merely to collect it.
Empty/full/rejected piles are bounded. A danger-cancelled menu is retryable after
recovery. A deliberate bypass/withdrawal defers the interrupted optional destination
until newly mapped terrain or XL progress. LOS loss, glyph changes and replacement
monster IDs cannot restore it. Deferral is floor-scoped and rebases with public
anchors; unrelated loot remains eligible.

`mibe_progression.py` deliberately targets only Dungeon/Lair. Lair readiness and
the five-floor return objective are policy, not proof of cleared floors. Portal
coordinates come from the current map frame. Stair destinations retain danger
memory. `escape_upstairs` uses an actually observed return link, recent clear
departure evidence, a departure-attack forecast and an explicit arrival uncertainty
allowance. Pinned `main.cc` tags followers before the departure tick; `files.cc`
charges 1.5 player-speed on arrival. Visible start-adjacent followers are rejected,
but invisible followers and unknown destination threats are not declared impossible.
After escaping, the reverse stair is banned to prevent immediate re-entry.
The observed floor transition completes the withdrawal goal. It never starts a
new retreat on the next quiet floor. Clouds alone blocking otherwise eligible
stairs use the existing bounded terrain-wait path; bans remain in force.
An unobserved ordinary pursuer only stops a return when its last-seen travel/reach
envelope could reach departure, or the required evidence is missing. Casters,
summoners and uncertain effects remain conservative refusals. Departure bursts
that can consume almost all current HP are refused even with a previously clear
destination. `mibe_stairs.py` separately permits one exhausted-alternatives probe
of a pair left by a full-health, quiet planned withdrawal; genuine emergency
escapes and adverse arrivals never supply that permission or erase normal bans.
Return links rebase with proven public anchors and are dropped on ambiguous resets.
Memory is per process; full inter-floor routing and automatic history restoration
on a new supervisor remain limitations.

Each action carries structured assessment, candidates, rejection reasons and its
actual objective. The recorder joins it to observed HP/formation/location changes;
loss of LOS is not a proven kill or successful escape. No LLM is involved in this
policy, and optional engines remain independent of WebTiles mechanics.

## Native routine automation

Validated `rest` and `explore` send native `5`/`o` (`cmd-keys.h`) rather than
queued policy waits/moves. Public `set_option` enables key cancellation and sets
travel/display pacing. Supervisor `delay` controls decisions; independent
`explore_delay` controls native exploration/downstairs tiles (default20ms,
validated0–2000ms, the engine limit). Decision-delay changes do not cancel travel;
tile-delay changes do. Recovery uses5ms native rest delay. Combat, poison
reassessment and guarded tactical navigation remain single-action.

Routine safe downstairs routing emits `travel_downstairs` with an exact public
map coordinate. The adapter sends `X`, individually acknowledged `>` selections,
then Enter only when the fresh native map cursor matches that ordinary staircase.
In0.34.1, `>` also selects hatches/branch entries (`viewmap.cc`); cursor verification
is mandatory. Public UI_VIEW_MAP=2 and CURSOR_MAP=2 delimit selection. Repeated
cursors/public-map bounds stop failed selection; changed danger or controls close
the observed map rather than queue gameplay keys. Native travel stops at the
same-floor coordinate; arrival is not descent. Only a fresh policy decision may
send `stairs_down`. Pause/step/reconnect retain consumed-key and no-replay guards.

The adapter requires the command's own NORMAL0 start acknowledgement before
accepting COMMAND1 as completion; an old ready snapshot cannot finish the next
`o`. Pending activity survives prompts/pages without replaying its start key.
Normal exploration has no periodic cancellation. Only explicit single-step has
a five-second cooperative bound. Public no-progress and cancellation waits remain
bounded; pings and repeated packets cannot renew them.

Pause/stop, danger and turn/recovery guards interrupt activity with native
**Ctrl-R (18), redraw**, then await its own NORMAL/COMMAND acknowledgement.
Native `kbhit()` leaves the interrupt queued: travel stopping at COMMAND does not
yet prove the key was consumed. A redraw is valid and turn-free if completion
races it; Escape remains for real menus/prompts. In this build Ctrl-L is attack
right, not redraw. Transport failures never replay the original macro.
`Observation.native_automation`/`native_result` carry the outcome; interface
acknowledgements preserve it until the next gameplay action.

One logged command/step may span multiple native turns. Turn guards are checked
at public updates, not hidden engine ticks: the live 220-turn smoke stopped at
228. Record actual advancement and censoring rather than assuming an exact cap.
The smoke now counts meaningful game turns separately from advancing commands.

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
