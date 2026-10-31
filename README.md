# DCSS Bot

An autonomous DCSS bot with modular species/background policies. The first and
currently only built-in profile is **MiBe: Minotaur Berserker of Trog**, controlled
through the official WebTiles protocol and watched in the native graphical browser
client. No paid API keys. DCSS **0.34.1** is pinned to commit
`1eebc1a2892e1c89776a0d7a10691f8dac8d9796`; the engine is not modified.

**Gameplay status:** this is experimental, not a demonstrated survival improvement.
The latest matched eight-seed development sample reached D:5 in 1/8 runs versus
baseline 3/8, and all eight candidate runs safety-paused. D:10/Lair were not
reached. The final 30-game comparison was not run. See the
[stabilized handover](docs/verification.md#stabilized-handover) for tested evidence,
remaining failures and the archived unfinished work.

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

### Direct LAN spectating on this host

Open **http://10.1.1.232:8080/#watch-MiBeBot** from the same LAN. No SSH tunnel or
bot login is needed. A host-local systemd user socket forwards that specific
private address to the existing loopback server; the container and bot connection
remain unchanged. See [LAN forwarding](infrastructure/README.md#private-lan-forward-on-this-host)
for service controls. Off-LAN clients need a route to this private network or the
SSH tunnel above; Tailscale alone does not automatically route the LAN subnet.

## Controls

In a second Linux terminal (or over SSH):

```fish
uv run --locked dcss-bot status
uv run --locked dcss-bot pause
uv run --locked dcss-bot step
uv run --locked dcss-bot resume
uv run --locked dcss-bot delay 1
uv run --locked dcss-bot delay 0
uv run --locked dcss-bot explore-delay 0.02
uv run --locked dcss-bot stop
```

`pause` preserves the live game connection. `step` executes one decision/native
command and stays paused: menu inspection may cost no turn, while native `5`
rest or `o` exploration can advance several turns. Pause/stop interrupt active
native automation with a turn-free redraw and await its consumed input boundary.
Escape is reserved for menus/prompts. Already accepted turns cannot be undone.

`delay` controls the pause between decisions (default `0.4` seconds), not each tile
of native travel. `explore-delay` independently controls native exploration and
downstairs travel (default `0.02` seconds per tile, range `0`–`2`). Changing
decision pacing does not interrupt travel; changing tile pacing interrupts it at
a safe boundary before applying the new speed. Normal native `o` continues until
the game stops it; only explicit single-stepping has a five-second cooperative
bound. Recovery is deliberately fast (`rest_delay=5` milliseconds). `delay 0`
accelerates decisions; combat stays single-action.

`stop` saves/disconnects; it does not abandon or kill the character. Ctrl-C also
requests graceful stop. Start paused with `uv run --locked dcss-bot run --paused`.
Controls use a private local Unix socket at `state/control.sock`, not an exposed
HTTP API. Only one supervisor may own that socket. For separate simultaneous
experiments, specify separate usernames and `--control-socket` paths.
`--control-socket` is a global option and must come **before** the subcommand.
Address that same socket for every control command, for example:

```fish
uv run --locked dcss-bot --control-socket state/experiment.sock status
```

Stop infrastructure **after** stopping the bot:

```fish
docker compose down
```

For the bootstrap's Podman container: `podman stop dcss-webtiles`.
Persistent `state/` is not deleted by these commands.

### If the bot looks idle

Check `dcss-bot status` before intervening: `paused`, `state`, `reason`, `turn`,
`actions` and `objective` distinguish an intentional pause from recovery or pacing.
If turns advance while the objective is `recover`, the bot is taking in-game rest
turns; it is not paused. Those turns are still recorded.

A safety pause deliberately stops new actions. Inspect the reported reason and
the run's `events.jsonl`/`actions.jsonl` before resuming; repeatedly resuming an
unresolved prompt or cycle guard does not fix it. Action/turn limits instead end
the invocation and record the result in `summary.json`.

“No bot control socket” means no supervisor is reachable at the selected path,
not necessarily that the game server is down. Check which socket was used. To
reattach a saved game after its supervisor exits, use `run` with the same account,
credential directory and game mode; `resume` only controls a running supervisor.

## Logs and playable recordings

Each `run` invocation creates `runs/<UTC-timestamp>-<id>/` containing:

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

The supplied server exposes both normal `dcss` and custom-seed `dcss-seeded`
entries. An older image without the seeded entry needs rebuilding; ordinary
evaluation does not require a server restart. After infrastructure changes, stop
bots first, stop the container, then run `python3 scripts/server.py --rebuild`
(or `docker compose up --build -d webtiles`). Saves remain under `state/`.

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

For frozen-source comparisons, `scripts/benchmark.py` snapshots the package before
starting separate fresh-account workers. Development defaults to seeds 1–8;
held-out evaluation defaults to 101–130. Do not tune against held-out traces.
Use the same turn/action/wall-clock bounds for both sides:

```fish
uv run --locked --extra video python scripts/benchmark.py launch --label development --source src/dcss_bot --output runs/benchmarks/development --split development --concurrency 8 --max-turns 8000 --max-actions 20000 --seconds-per-run 1800 --record-seeds 6
uv run --locked python scripts/benchmark.py launch --label heldout --source src/dcss_bot --output runs/benchmarks/heldout --split heldout --concurrency 8 --max-turns 8000 --max-actions 20000 --seconds-per-run 1800
uv run --locked python scripts/benchmark.py report --baseline runs/benchmarks/BASELINE --improved runs/benchmarks/heldout --output runs/benchmarks/comparison.json
```

Each output root must be new. Preserve the baseline snapshot **before** modifying
policy. Reports validate public starting conditions/provenance, give Wilson
intervals for observed milestone fractions and an exact two-sided paired McNemar
test. These describe attainment **under the run bounds**, not eventual survival;
pauses/caps are not wins. P-values are unadjusted for multiple comparisons.
Use equal concurrency and avoid other play/recording during formal comparisons;
capture representative development games separately.
Completed large JSONL files are losslessly gzipped. Keep each benchmark's private
credential directory and socket alias if you intend to resume its saved games.

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

Resume its saved character later with the same account and credential directory,
**without a seed request**. Explicitly clear `BOT_SEED` so a value in `.env` or
the shell cannot accidentally request a new seeded run, and select the saved
game's mode:

```fish
env BOT_SEED= DCSS_GAME_ID=dcss-seeded uv run --locked dcss-bot --control-socket state/seed-demo.sock run --username SeedDemo --paused
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

For `run`, supplied CLI options override environment values, which override `.env`.
Useful flags: `--character mibe`, `--agent rule`, `--delay 0.4`,
`--explore-delay 0.02`, `--max-turns 10000`, `--max-actions 10000`, `--record`,
`--paused`. Turn limits count turns played during this invocation, not a resumed
character's lifetime. `EXPLORE_DELAY` configures native tile pacing independently
of `ACTION_DELAY`.

`evaluate` shares connection, character and storage configuration, but takes its
engine list, pacing, limits and recording choice from its own `--agents`, `--delay`,
`--max-turns`, `--max-actions`, `--seconds-per-run` and `--record` options. Those do
not inherit `BOT_AGENT`, `ACTION_DELAY`, `MAX_*` or `RECORD_VIDEO`.
Its optional `--explore-delay` overrides `EXPLORE_DELAY`; otherwise native travel
uses that configured speed. For fully accelerated evaluation, supply both
`--delay 0 --explore-delay 0`.

Generated credentials live **beside the control socket**, with mode `0600`.
The default is `state/credentials-USERNAME.json`; changing the socket's directory
changes the credential lookup directory. Preserve that directory when resuming.
Smoke-run credentials are under `.runtime/`, not `state/`; do not discard them
as build caches if you intend to resume those accounts.

Passwords never enter run metadata. `.env`, state, recordings and caches are
ignored. Keep backups private: server state contains accounts and saved characters.

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

Only MiBe is implemented; other combinations need their own policies and verification.
Modules using the existing action vocabulary need no adapter/supervisor changes.
New mechanics such as spellcasting should extend shared actions, not add per-combo
transport branches.

### Current MiBe policy

The `rule-v4` policy evaluates a typed short-horizon threat assessment before each
consequential action. It considers public monster descriptions, attack tables,
speed, reach, ranged/disabling/summoning threats, recent damage, statuses,
formation, known retreat terrain and available resources. Physical attack maxima
no longer stand in for means: displayed rows supply a conservative rolled-damage
approximation while burst maxima stay intact. Missing tables retain their
uncertainty bound. Expected losses and plausible bursts are heuristics, **not a
combat simulator**.
Displayed projectile and distant-spell damage is separate from physical melee.
Verified ordinary throwers cannot keep shooting while adjacent; ranged
specialists and uncertain types retain conservative bounds.
`Threat.ranged` records exposure during the forecast horizon, not merely whether
the enemy is currently within shooting distance.
Harmless butterflies remain visible obstacles but are not fast melee threats:
the pinned engine gives them no attacks. Public Throw Ally/Battlecry capabilities
represent possible ally relocation/Might, not invented summons, haste or active
buffs. Clockroach range warding blocks nonadjacent HP damage, not all control:
the policy retains roots/corrosion and status darts while avoiding known
damage-only ranged spending.

Candidate actions and rejection reasons are stored with the chosen action; the
recorder links that decision to the next observed outcome.

Approach safety counts enemies that can reach contact during the next reaction,
not only those already adjacent to the proposed tile. Bounded holds preserve
formation without renewing an exhausted stationary-wait budget. If standing
permits multiple arrivals, a tighter-budgeted defensive step can reduce them to
at most one without approaching an enemy. Remaining poison damage is reserved;
hard control, ally relocation and unresolved threats cannot certify this fallback.

Combat is single-action: attack, wait, lure, reposition, use a resource, then
observe again. Following enemies and ineffective retreat attempts limit repeated
backpedalling under attacks of opportunity. Merging slimes and hydras with an
unsuitable axe receive explicit avoidance policies. Trog's Hand prepares
regeneration/Will, Brothers in Arms spends available piety on unacceptable fights,
and Berserk requires both a **difficult fight** and a bounded commitment with an
acceptable aftermath. A dangerous multi-turn forecast alone is insufficient:
substantial public attack bounds/group damage or significant observed HP loss
must justify spending rage. A native “High” label alone is insufficient. Ordinary
weak groups and a healthy character's lone weak-snake encounter use normal defence.
Poison, exhaustion,
unresolved enemies and delayed teleport still restrict commitment.
Active Berserk preserves ordinary melee commitments instead of backpedalling
until rage expires. A known single-front queue or small fully visible wave can
justify early rage only after the difficulty check. One weak future poison source
requires an HP reserve; ongoing poison and unresolved threats remain guarded.
High-risk melee preferences are distinct from hard harmful interactions. An
already-Berserk character may finish an examined ordinary caster fight without
the preference first forcing retreat and then forbidding its attack. Hydra,
escalating-slime, dangerous-weapon, poison/control and axe-cleave guards remain.
This does not authorize starting rage against an unexamined caster. Native
“Berserker Rage” is a self-enchantment, not a fictional ranged damage spell.
A lone, fully examined low-HP ally-throwing leader can receive preparatory rage
from three tiles away only with at least 75% HP, a current visible two-step
approach, bounded activation exposure and an aftermath reserve. A visible army,
unresolved foe, poison, cooldown or missing staging terrain removes that option;
it is not permission to charge a supported group.


One explicitly bounded caster case is also supported: a nearby, publicly examined
low-HP Dart/Slow/Call Imp/Throw Flame caster with at most one weak companion. Blink,
invisibility, hard control, poison and unobserved spell data do not receive that
exception. Missing public ability data, piety-rank changes and recovery from
native `Conf` or `-Berserk`/`CD` trigger a bounded turn-free ability refresh;
unchanged empty responses cannot create a zero-turn loop.
When useful routes and resources produce no action, an examined low-HP short
book can receive a finite close from at most five squares away. Plain `Slow`
is not Berserk cooldown, but this action still budgets 1.5 world turns, retains
the displayed raw projectile mean without an evasion discount, requires a
nonlethal maximum burst, and reserves at least 40% maximum HP or one maximum
body hit after expected damage. That reserve is not a survival guarantee.
Each close must show fresh distance progress; failed movement or changing native
IDs cannot replenish its initial distance-minus-one action allowance. This does
not authorize rage while slowed. A displayed minor-only summoner with one
examined weak summon can instead justify one available Trog's Hand attempt,
reserving two maximum summoner melee hits, followed by at most one clear-tile
interception outside the summoner's melee. Neither action renews a spent
summoner approach or authorizes chasing Blink; hard control, unknown/greater
summoning, dangerous brands, low HP, restraint and pending teleport remain guarded.
Stationary luring is limited by observed approach, not a timer that can be
replenished by a bat circling at the same distance or by free inspections.
A separate bounded shooter approach requires one fully examined, publicly bounded
ordinary projectile/pure-damage target and a visible clear closing path. It starts
at least 80% healthy, spends at most six actions/eight turns per encounter, and
requires remaining expected loss and the next-position single-attack bound each
to fit current HP with a 15% max-HP reserve. Each step needs fresh displacement
and closing progress. A brief additional/unresolved foe suspends execution without
refilling or automatically discarding an otherwise retained allowance; its
original clock keeps running. Lost target LOS, failed/kiting movement, changed
unsafe mechanics or exhausted action/time/HP bounds still cancel it. Native
client-ID aliases prove neither extra bodies nor persistent identity and cannot
refund an encounter budget.
Missing descriptions trigger free reexamination. Only route cells—not every
neighboring cell—must be visible and clear; known open doors are legal ground.
This is not a kill or survival guarantee.
An issued movement result is consumed once; a later deliberate withdrawal is not
another result of that move. A still-admitted approach is not reversed merely to
return to its old stair, and an admitted manageable shooter alone is not a reason
to evacuate a floor. Fresh safety/resource overrides retain priority.



Moving retreat forecasts follow an actual known upstairs/cover route, not three
stationary turns at the next tile. A retained
goal must make observed progress; losing LOS does not automatically abandon it.
At most one fully examined plain low-damage melee interceptor per forecast step
may be charged against the retreat damage budget, including possible opportunity
attacks. Strong, reaching, poison, control and other nonplain threats remain excluded.
An observed floor transition completes that withdrawal; the bot must not chain
retreats up clear floors and ban every return staircase. Temporary clouds blocking
otherwise eligible stairs cause bounded waiting, not false exploration exhaustion.
Cover remains an intermediate escape waypoint. Native monster IDs can change
after LOS loss; free inspection refreshes the new instance without cancelling a
progressing route. Fresh invisible-attack messages and unexplained damage remain
threat evidence even alongside visible weak enemies; no hidden location is invented.
Fresh unlocated melee can trigger a finite native Ctrl-direction search without
walking into guessed squares. Each further attack needs fresh evidence; an axe
cleave hitting “something” does not identify or locate the invisible creature.
A plain movement key that fails to displace while producing fresh anonymous
outgoing melee is uncertain blockage evidence, not proof of an invisible creature's
identity or position; it is distinct from deliberate native Ctrl-direction attacks.

A narrowly certified, observed sealed undead pocket need not stall exploration.
The bot can retain its historical wall evidence while moving away, invalidating
it on new threats or contradictory observations rather than inventing visibility.
A separate finite walking/projectile separation proof can authorize one visible
dry nonapproaching step, not permanent enclosure. Reach-two walking bounds use
known barriers conservatively (save34 lava30/deep water31; shallow water32 is
passable). Ordinary net-only throwers additionally require every optimistic firing
origin/path to exclude exposure within range eight: one departure action at the
current endpoint and four actions at the next. Their restraint capability remains.

After a fight, full HP and the end of slowing do not mean Berserk is ready.
Quiet HP, slowing and cooldown recovery use native **`5`**, which stops for native
recovery events or danger; observations are reassessed between commands. Poison
retains one-turn treatment/reassessment. A stale cooldown has an 80-player-turn
policy budget, rather than 80 native commands, and eventually pauses diagnostically.
Visible nets/webs have an explicit struggle decision: a native movement key cuts
or escapes the restraint instead of moving. Known emergency resources still take
precedence, and the immobile turn does not blacklist an actual floor step.

On clear ground, development selects exclusive skill targets, compares axe damage
and delay at the observed skill level, fills useful defensive equipment slots,
and uses native identification/enchantment menus. Ordinary brands/armour egos are
read from their displayed names; unrankable artefacts and hazardous equipment are
rejected. Jewellery fills empty slots only. Unknown potions are not blindly drunk.
Unknown scroll identification is bounded and requires full health, an observed
quiet interval and a known ordinary stair anchor; it remains intrinsically risky.
**Spotted scrolls and potions are explicit destinations before more exploration
or distant equipment.** Their public-map locations survive loss of LOS and
verified coordinate rebases; a safe visible next step is still required. Mixed
pickup piles collect unidentified supplies without using them, plus a verified
equipment upgrade. Empty/full/failed piles are bounded; danger interruptions do
not permanently retire their supplies. A rejected pursuit remains deferred, not
an excuse to walk straight back into the encounter.

Routine safe exploration prefers native **`o`**, including autopickup and travel,
even when our fallback frontier finder has no target. Native **“Done exploring”**
allows safe progression instead of a speculative corner sweep. Benign stops can
resume `o` on already mapped ground. Remembered dangers, hazards and deferred loot
retain guarded manual routing; partial/no-progress results use bounded fallback
frontiers, doors, cloud waits and diagonal probes. Position caches translate only
with proven public terrain anchors. Urgent tactical actions always take precedence.

After exploration completes, travel to an eligible ordinary downstairs uses
native **`X`, `>`, Enter** rather than repeated movement decisions. The adapter
checks the actual map cursor, cycling `>` if needed; hatches, branch entrances
and remembered dangerous stairs are not interchangeable targets. Arrival stops
on the staircase. Descending is a separate decision with a fresh danger check.
Tactical hazards retain guarded manual routing.

The initial route is Dungeon and, with sufficient XL/armour and a poison response,
Lair. Other branches are not deliberately entered. Dangerous stair destinations
are remembered. Routine stair commands still reject visible hostiles. A separate
`escape_upstairs` action can return through a recently observed ordinary upstairs
when no visible enemy is adjacent and estimated traversal risk fits the policy
budget. Departure attacks, arrival uncertainty and the abandoned destination are
recorded; immediate descent back into that encounter is blocked. **Previously
clear is not guaranteed safe.** Tactical stair memory is per supervisor process.
There is no complete inter-floor escape planner, return routing to a previously
passed Lair entrance, rune collection or winning play.

This remains an experimental survival policy. Cover, threat and equipment
estimates can be wrong; an unknown prompt or encounter without a credible legal
action can pause the run. No survival or win-rate claim follows from passing an
opening smoke. Never use a valued character as a disposable benchmark.

Tactical thresholds are typed policy configuration:
`RuleBasedAgent(tactical_config=TacticalConfig(...))`. Installed strategies can
choose these values without changing transport code; no extra environment
variables or model provider are required.

## Extend and test

Contributors and coding agents should read [AGENTS.md](AGENTS.md) for component
ownership, protocol invariants, safe test isolation and the development workflow.

Implement `async decide(observation) -> Action`, using the immutable public models
in `src/dcss_bot/models.py`. Run an installed strategy with
`--agent your_package.your_module:YourAgent`. Strategy code cannot send WebSocket
messages through this interface; actions are validated and dispatched by the
adapter. No provider credentials or SDK are required by the core.
An LLM backend is not bundled; endpoint/model/key configuration belongs in a
strategy plugin. Provider exceptions and invalid actions cause a logged pause.
The provider-neutral extension contract is described in the architecture document.
Import the MiBe implementation from `dcss_bot.characters.mibe` and shared danger
annotations from `dcss_bot.danger`. `dcss_bot.agents` only loads decision engines;
it does not export character strategies or equipment preferences.

```fish
uv sync --locked
uv run --locked pytest -q
```

Tests cover protocol deltas/visibility, real loopback WebSocket connection and
reconnection, UI handling, action validation, survival regressions, lifecycle
controls and recorder metrics. A live upstream server is required for the full
integration smoke; mocked protocol tests are not its replacement.

With the local server and video prerequisites ready, reproduce the known seed-8
opening acceptance scenario:

```fish
env BOT_CHARACTER=mibe BOT_AGENT=rule BOT_SEED=8 uv run --locked --extra video python scripts/smoke.py --record --turns 130
```

This creates a fresh account and private `.runtime/smoke-...sock`, verifies
character creation and pause/resume/step, plays real turns, checks logs, and opens
the finalized WebM in Chromium to prove it decodes. It fails explicitly if the
agent unexpectedly pauses. Seed 8 is an exercised acceptance case, not a general
survival guarantee; use additional held-out seeds when evaluating strategy changes.

`--username ACCOUNT` can resume a prior smoke save. Clear `BOT_SEED` and set
`DCSS_GAME_ID=dcss-seeded` when resuming a seeded one. Keep its credentials in
`.runtime/`; the smoke helper does not use the normal `state/control.sock`.

### Recorded verification

Run the survival-policy suite with `uv run --locked pytest -q`; dated receipts
are linked below. A fresh seed-8 recorded run completed
**160 turns, 159 turn-advancing decisions and six kills**; its save
then resumed for **30 more turns and four kills** under the updated adapter.
Pause/resume/step, native anonymous spectating and pause-aware WebM playback were
exercised. These are integration checks, not evidence of a survival win rate.

Earlier milestones exercised Firefox on Linux, container restart persistence and
real character death. Infrastructure was verified with rootless Podman; Docker
Compose, native host compilation and MacBook SSH authentication were not exercised
here. Commands, dated results and artifact paths are in [verification](docs/verification.md).
Runtime artifacts are gitignored and may be absent in another checkout or after
cleanup. Historical handover notes do not establish that a bot is currently running.

See [architecture and research](docs/architecture.md) for component boundaries,
licensing, integration tradeoffs and the visibility contract.
