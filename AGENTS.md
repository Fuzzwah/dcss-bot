# Working on DCSS Bot

## Goal and current scope

Build a locally runnable, observable autonomous Dungeon Crawl Stone Soup agent.
Survival and understandable decisions matter more than speed or kill count. A human
must be able to watch the real game through native WebTiles without controlling the
bot's authenticated session. Default play requires no paid API keys.

The current deterministic policy is `rule-v4` Minotaur Berserker of Trog.
`mibe` is currently the only implemented character profile. Other profiles and
engines are extension points, not already-supported gameplay. No LLM backend,
spellcasting policy, learning system or exact deterministic replay is bundled.
Keep improving MiBe without baking its choices back into shared infrastructure.

Read [README.md](README.md) for operation, [architecture](docs/architecture.md) for
protocol/research decisions, and [verification](docs/verification.md) for dated
results and known limitations. Verification documents are history, not proof that
an old process is still running or its gitignored artifacts still exist.

The 2026-10-31 optimization pass ended with a stabilized, tested policy, **not a
proven gameplay improvement**: latest development D:5 attainment was 1/8 versus
baseline 3/8, with all candidate runs safety-paused. The final 30-game comparison
was not run; individual held-out traces remain unreviewed. Unverified development18
work is archived outside active source/tests at
`.runtime/mibe-development18-set-aside/manifest.json`. Do not describe or enable
it as completed functionality. See the latest verification handover before
starting further optimization, and agree a bounded evaluation scope first.

The subsequent bounded routine-play fix adds explicit scroll/potion destinations,
less eager Berserk, native `5` recovery and guarded native `o` exploration. It is
not the archived development18 policy or a new survival benchmark. See the latest
dated smoke receipts; do not infer a better win rate from these mechanism checks.

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
  `characters/mibe.py`: MiBe `PROFILE` and `RuleBasedAgent` orchestration.
  `mibe_tactics.py`: threat/candidate assessment and tactical memory;
  `mibe_mechanics.py`: pinned mechanics/public monster catalogue;
  `mibe_equipment.py`, `mibe_development.py`, `mibe_progression.py`: policy modules;
  `mibe_exploration.py`: per-floor position caches and public-anchor rebasing.
  `characters/__init__.py`: explicit built-in registry / `module:PROFILE` loader.
- `agents.py`: `make_agent(identifier, character)`; `rule` calls the selected profile's
  factory. `module:Class` engines retain a no-argument constructor and `async decide`.
- `danger.py`: shared public-state risk annotations. Character tactics stay in profiles' policies.
- `supervisor.py`: single-flight execution, pacing, controls, bounded failures and saving.
- `recording.py`: JSONL, metrics and pause-aware anonymous-spectator capture.
- `evaluation.py`: isolated fresh-account seeded trials, provenance and comparisons.
- `item_names.py`: visible item classes and terse/verbose label normalization;
  never expose wire subtype, unidentified plus or identification flags.
- `scripts/benchmark.py`: frozen-source concurrent fresh-account trials, streamed
  trace analysis, milestone Wilson intervals and paired-start checks.
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
8. **Preserve survival safeguards.** Routine stairs reject visible hostiles.
   Explicit `escape_upstairs` requires actual ordinary upstairs and rejects visible
   adjacent followers; strategy must also score exposure and remembered arrival.
   Destinations and following enemies are not assumed safe. Keep regression coverage
   for merging slimes, low HP, unsafe stairs, ranged luring, ineffective pursuit,
   liquid fire and reach attacks. Do not cure a stall by disabling its safety bound.
   Quiet `-Berserk` recovery uses native `5`; do not open a fresh encounter merely
   because HP and movement speed recovered. The native cooldown can last 28–72
   base-duration units; the default policy budget is 80 elapsed player turns,
   not 80 macro commands. Combat/escape preempt recovery; stale status is bounded.
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
11. **Native item identity has two forms.** Inventory brands such as `(flame)` and
    ordinary armour egos such as `{rF+}` are meaningful, not decorations to strip.
    Menus render verbose equivalents. Use shared normalization and visible class,
    then validate a unique enabled hotkey in the actual operation menu. Supply and
    equipment letters overlap; `curing` is not a ring. Scroll guards must accept
    plural stacks. Native confirmations can require uppercase `Y`/`N`.
12. **Combat actions affect more than the selected target.** Axe cleave must
    consider every adjacent hostile, including a hydra beside an ordinary target.
    Do not repeat Might/Resist potions while their native status is active.
    Overview resistance snapshots become unknown after equipment/XL/status changes.
    Keep high-risk `avoid_melee` preferences separate from hard
    `melee_prohibited` interactions. Already-committed examined caster continuation
    never overrides hydra/slime/brand, poison/control or axe-cleave safety.
13. **Benchmark without leakage.** Keep development seeds separate from held-out
    seeds, freeze source before launch, and preserve exact bounds/provenance.
    Review held-out deaths only after final policy freeze. Postmortem `{unknown}`
    consumables were not identified resources available to the policy during play.
14. **Interpret public mechanics before adding tactics.** Native monster spell
    `range_string` is rendered information; retain it in `Monster.spell_ranges`.
    Freeze is contact-only, not a seven-tile shooter. A displayed physical attack
    table beats an unknown-creature damage allowance. Berserk rescales HP;
    draining also changes maximum HP but must not hide actual incoming harm.
    Last-seen danger footprints must allow an already-enclosed player to leave
    without enabling later re-entry. Test actual captured encounter transitions.
15. **Stair costs are split across floors.** Pinned `main.cc` tags followers
    before the departure tick; `files.cc` applies a 1.5 multiplier on arrival.
    Ordinary unslowed traversal was observed at 25 aut; slowed at 37 aut.
    Account for attacks before departure, possible tagged followers and arrival
    uncertainty. Return links require an actually observed stair pair, proven
    coordinate rebasing and recent clear-departure evidence. Never infer them
    from a save or advertise reconstructed tactical memory on ordinary restart.
16. **Do not add alternative attacks.** A kobold's whip6 plus stones2 is not an
    eight-damage projectile. Native spell dice provide their own mean/maximum;
    Sigmund's distortion scythe must not become his flame-spell damage. Unknown
    formulas retain uncertainty. Summoning/support spells are not direct beams.
17. **Retreat needs a destination and moving forecast.** Maintaining a
    nonadjacent equal-speed gap is not failed escape. `mibe_retreat.py` finds a
    real known upstairs/cover goal; the controller retains it, checks progress,
    and reobserves after one action. Do not silently weaken AoO/faster-pursuit
    guards or confuse one-step admissibility with a guarantee for the whole route.
    Native nonattacking Berserk turns incur an increasing penalty (`main.cc`);
    ordinary backpedalling after invoking rage wastes the commitment.
    Cover is an intermediate waypoint, not permission to reopen the encounter.
    Native monster IDs can change after losing LOS: inspect the new instance
    without discarding a still-progressing withdrawal goal or inventing its location.
    A successful observed floor transition completes withdrawal; do not carry the
    old goal into a new quiet floor and manufacture a retreat/ban chain.
    Planned walking escape normally excludes reachable contact: native opportunity
    attacks may follow approach. At most one fully examined plain low-damage melee
    interceptor per forecast step may be damage-budgeted, including opportunity
    attacks; strong, reaching, poison, control and other nonplain threats stay excluded.
    A distant faster pursuer is not an automatic refusal: it must pass the same
    time-dependent interception bound on every forecast step. Only the next step
    is authorized, not the unscored remainder of a long route.
18. **Invisible attacks need message evidence.** Native “out of thin air” spells
    count alongside “Something hits”. Fresh unexplained harm adds to visible
    threats; it must not vanish because a trivial enemy is also visible.
    Same-turn damage survives zero-turn UI, but UI cannot renew the original
    unseen-attack timestamp. A new undamaged world turn clears that turn's loss.
    A last-seen possible occupied square is not a confirmed clear route.
    Native Ctrl-direction `Action("attack", direction)` swings without walking.
    Fresh player-on-“something” feedback permits one command retry; menus, reused
    messages and empty swings cannot renew it. Axes can cleave a secondary target
    while their commanded square is empty: never infer its coordinate/identity or
    refresh a former monster's last-seen timestamp from this response.
    Unlocated melee searches are finite and require fresh public evidence.
    A plain move that fails to displace with fresh anonymous outgoing melee gives
    uncertain blockage evidence only, not invisible identity/position. Keep this
    separate from deliberate native Ctrl-direction attacks and their retry budget.
    Native unseen deaths may say “You feel a bit more experienced.” Stop renewal
    and clear stale harm only without fresh incoming evidence; a named kill must
    not erase a second hidden attacker.
19. **Held is not paralysis.** Pinned `movement.cc:963–968` makes movement
    struggle before displacement/attacking. Use the explicit escape-restraint
    objective, keep emergency resource precedence, and do not record that
    immobile turn as a failed route step. Wait (`.`) does not cut the net.
20. **Identification can stale every matching appearance.** `tileweb.cc` compares
    names under current global knowledge; letter changes can omit the renamed
    label. Refresh native inventory before known Identify; reconcile only unique
    letter/class/available-quantity matches. Operation source/target identity
    checks still require the actual current visible label; never relax them.
    Native paged inventory initially exposes only Gear. Visit each nonempty
    Potions/Scrolls/Evocable Items category, stopping at wrap; shrink stale menu
    tails when `total_items` decreases. A supply renamed by global identification
    cannot be refreshed from Gear alone.
21. **Observe before every routine/free action.** A new ability or item menu must
    not overwrite the previous world snapshot before harm, kills and floor
    transitions are processed. Observation is idempotent for the same immutable
    snapshot; reused assessment evidence must belong to that exact observation.
22. **Rage overrides all ordinary backstep paths.** The emergency fallback once
    escaped the ordinary-rage guard and alternated approach/retreat until rage
    expired. Keep its captured low-HP regression. The bounded low-HP caster
    exception uses public approximate HP and the shown spell book, never a
    general permission to Berserk into disabling casters or summoners.
23. **Physical maximum is not physical mean.** Pinned `attack.cc:918–944`
    rolls natural, weapon and enchantment damage. `EnemyMechanics.melee_mean`
    approximates displayed attack rows; unknown tables retain conservative bounds.
    Keep burst maxima intact. Native “Low” threat must not downgrade a known
    high-damage branded weapon to trivial.
    Projectile/distant-spell maxima are separate from physical rows; contact
    spells still belong in the close envelope. Ordinary adjacent shooting is
    restricted by native `mon-act.cc:1507–1523`; retain specialist/armed exceptions
    and uncertainty for unknown types.
    `Threat.ranged` means forecast exposure, not a static current-distance check.
24. **Stair bans are not proof of permanent impassability.** `mibe_stairs.py`
    permits one exhausted-alternatives probe after an actually observed quiet,
    full-health planned departure. It leaves ordinary bans intact and never
    probes an emergency escape pair. Missing last-seen positions cannot certify
    no followers; distant ordinary enemies need a conservative travel/reach bound.
    A healthy quiet landing is not a clear departure receipt. Reconstruct actual
    quiet history and spent/revoked probes; zero-damage unseen attacks still
    invalidate clearance. Check disconnected public terrain before blaming routing.
25. **Stationary luring needs observed progress.** `EncounterProgress` exhausts
    three unproductive world waits. Sideways movement, elapsed time and free UI
    do not refill that budget. A finite wait is not proof that its later advance
    was tactically successful; verify the follow-on native behavior.
    Bounded shooter approach requires actual complete public offensive data, one
    examined bounded target and fresh observed closing progress. Its six-action/
    eight-turn encounter allowance cannot reset on native-ID churn; expected
    remaining loss plus 15% max-HP reserve is not a kill or survival guarantee.
    Native IDs reset outside seen cells (`player-notices.cc:551–557`); stale aliases
    prove neither additional bodies nor persistent identity. Preserve a sole
    same-name visible encounter's allowance without refilling it, using observed
    unresolved counts rather than raw old-ID cardinality. Missing descriptions
    await free inspection; exact public coordinate rebases preserve the budget.
    Require visible clear route cells, not visibility of every neighboring cell.
    Consume each issued movement receipt once, not again after a later retreat
    returns to its origin. Do not reverse an admitted active approach for optional
    stair positioning or evacuate manageable floors merely because a shooter exists.
    Single-attack reserve is position-specific; absolute multi-attack bursts remain
    logged uncertainty, not a survival guarantee.
26. **A sealed pocket needs observed evidence, not guessed immobility.**
    `EnclosureMemory` starts only from a fully visible, examined ordinary-undead
    enclosure. It may retain unchanged known walls outside LOS while the same
    occupant remains observed. Contradictions, capabilities/identity changes,
    disappearance, new unsealed threats and floor/frame ambiguity invalidate it.
    Never change visibility flags or omit the observed monster. Verify multiple
    follow-on moves: one legal bypass previously became a period-five loop.
27. **Temporary native restrictions must not poison ability caches.** Native
    `Conf` is confusion, not just the word “confused”. At development12 turn3868,
    an inspection correctly greyed every Trog power; the old rank-only cache
    retained that result after recovery. Defer inspection during restrictions,
    refresh afterward, and refresh on native `-Berserk`/`CD` recovery or observed
    public ability-data loss without a rank change. Failed/empty UI responses
    must not create a zero-turn inspection loop.
28. **Finite walking separation is not a cage certificate.**
    `WalkingDistances` uses known land barriers only for examined mundane land
    movers; unknown cells and closed-door opening costs remain optimistic.
    Reach-two bounds include optimistic attack squares across barriers. Flying,
    blinking and unknown movers retain conservative bounds. Save34 terrain IDs
    are lava30, deep water31 and passable shallow water32.
    A partially observed moat may justify one dry nonapproaching step while
    contact is beyond the forecast, never a permanent ignore or hidden-map fill.
    Net-only bypass also needs a finite optimistic projectile-origin/path proof
    with range upper bound eight: a separate one-action current-endpoint departure
    window and four-action next-endpoint window. Retain real restraint capability;
    neither this proof nor walking separation implies permanent enclosure.
29. **Movement control need not deal HP damage or check Will.** Pinned Ensnare
    creates a web with zero direct damage. Preserve its independent restraint
    risk; do not inherit a physical bite as a distant spell or treat Will as
    protection against webs.
30. **Optional loot must not reverse a withdrawal indefinitely.** Remember the
    exact destination; a bypass/retreat defers it until newly mapped terrain or
    XL progress. LOS/glyph/native-ID changes alone are not progress. Rebase that
    floor-scoped memory using the same public anchors as navigation. Native
    development13 acquired scale mail, then repeatedly reopened a gnoll encounter
    to chase a different fading leather armour; increasing loop limits is not
    a fix.

31. **Count arrival fronts, not only current contacts.** A legal step can put
    three already-public bodies in melee immediately afterward with little
    current-frame projected damage. Ordinary approach/hold caps remain two;
    a poison-reserved defensive step may instead reduce several arrivals to one.
    Do not reset stationary-wait evidence to make a hold available.
32. **Support is not damage, haste or invented monsters.** Throw Ally can
    reposition an existing body; Battlecry can grant Might. Public attack tables
    already include active buffs. Robin's small direct throw bound is specific
    to the pinned native mechanics, not a generic unknown-thrower allowance.
    Support invalidates ordinary formation certificates; lone low-HP leader
    preparation has separate route/HP/status bounds, not army-wide permission.
33. **Harmless speed and range warding are different facts.** Butterfly YAML has
    no attacks despite hostile wire attitude/250% movement. Retain the visible
    body without inventing melee damage. Clockroach range warding blocks
    nonadjacent HP damage but preserves non-damaging control; don't forbid all
    wands or disable legitimate adjacent-origin damage.
34. **Brief interruption spends the original clock.** A bounded shooter approach
    may suspend on an additional/unresolved foe without refilling its allowance.
    True LOS loss, failed/kiting movement, unsafe changes and expiry still cancel.
    Do not force the historical seed5 terminal: its unresolved hobgoblin, actual
    LOS loss and expired allowance were real, not an ID-renewal invitation.
35. **Native routine commands are interruptible macros.** `rest`/`explore` mean
    native `5`/`o`, not queued strategy turns. Require the command's own NORMAL0
    before accepting COMMAND1; old ready packets cannot end a new command.
    Normal exploration has no periodic cancellation. `yielded` is only the
    five-second explicit-step bound. Busy/command cancellation uses Ctrl-R18,
    not Escape or Ctrl-L12 (attack right in this build). Native leaves the key
    queued: await redraw's own NORMAL/COMMAND, not merely travel's stopping-ready.
    Actual prompts/menus still use Escape; retain no-replay reconnect. Public
    redraw granularity can overshoot turn caps. Keep decision `delay` separate
    from native tile `explore_delay` (default20ms, validated0–2000ms). Do not
    reintroduce400ms-per-tile pacing through the default0.4s decision delay.
    Decision-speed changes do not cancel native travel; tile-speed changes do.
    Routine downstairs travel uses `X`, verified/cycled `>`, Enter to an eligible
    ordinary stair. Native `>` includes hatches/branches: check the fresh map
    cursor against the exact public target. Stop on arrival; descent requires a
    separate fresh decision. Preserve danger exclusions and manual tactical paths.
36. **Scrolls/potions are routing goals, not just autopickup settings.** Remember
    public `?`/`!` glyphs, including legitimate remembered map glyphs after native
    travel/reconnect. Prioritize safe supply routes before more exploration and
    distant equipment. Preserve goals across LOS loss/rebases and optional-loot
    withdrawal deferral. A danger-cancelled pile is not an empty/full failed pile;
    mixed native floor menus need acknowledged multiselect, not bag letters.
37. **The fallback frontier finder cannot veto native exploration.** Try safe
    native `o` even without a custom frontier. Fresh “Done exploring” skips
    speculative corner sweeps; benign native stops with progress can resume on
    already mapped ground. Keep no-progress bounds and real tactical overrides.
38. **Native High is not a Berserk budget.** The captured decision was at full
    26/26 HP against a lone adder with maximum Bite4 and no recent damage.
    Require public attack/group/damage pressure, not the label alone.

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
- Keep the published WebTiles container port on loopback by default. The user
  explicitly approved LAN spectating at `10.1.1.232:8080`: host-local systemd user
  units `dcss-webtiles-lan.socket`/`.service` forward only that address to loopback
  without restarting the game. See infrastructure README. Spectators use their
  own anonymous connection, never the bot's login; bot controls stay on the local
  Unix socket. Do not widen this to public/all-interface access or Tailscale Funnel.
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
