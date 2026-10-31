"""Sequential, fresh-account seeded comparisons with bounded unattended runs.

Turn limits are right-censored observations, not wins. Matching observable hashes
is evidence of repeatability for that trace, not a promise of engine replay.
"""
import asyncio
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import importlib
import inspect
import json
import math
from pathlib import Path
import re
import signal
import statistics
from uuid import uuid4

from .adapter import ProtocolError, WebTilesAdapter
from .agents import make_agent
from .characters import BUILTIN_CHARACTERS, CharacterProfile, get_character
from .config import Config, DCSS_COMMIT, DCSS_VERSION
from .recording import Recorder
from .models import Cell, Observation
from .supervisor import Supervisor

def expected_version(text: str | None) -> bool:
    return bool(text and re.match(r"^Dungeon Crawl Stone Soup " + re.escape(DCSS_VERSION) + r"(?:\s|$)", text))



@dataclass(frozen=True)
class Evaluation:
    seeds: tuple[int, ...]
    agents: tuple[str, ...] = ("rule",)
    repeats: int = 1
    max_turns: int = 1000
    max_actions: int = 4000
    seconds_per_run: float = 600
    delay: float = 0
    record_video: bool = False

    def validate(self) -> None:
        if not self.seeds or any(type(seed) is not int or not 1 <= seed < 2**64 for seed in self.seeds):
            raise ValueError("Evaluation seeds must be integers from 1 through 2^64-1 (0 is random)")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Duplicate seeds: use --repeats for repeated trials")
        if not self.agents or len(set(self.agents)) != len(self.agents):
            raise ValueError("Provide distinct agent identifiers")
        if min(self.repeats, self.max_turns, self.max_actions) < 1:
            raise ValueError("Repeats and run limits must be positive")
        if not math.isfinite(self.seconds_per_run) or self.seconds_per_run <= 0:
            raise ValueError("Per-run deadline must be finite and positive")
        if not math.isfinite(self.delay) or not 0 <= self.delay <= 60:
            raise ValueError("Action delay must be between 0 and 60 seconds")


def _json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def observable_state(observation: dict) -> dict:
    """Exclude account/UI identity, prose and transient protocol monster IDs."""
    data = {key: value for key, value in observation.items()
            if key not in {"name", "messages", "menu", "prompt", "input_mode"}}
    data["cells"] = sorted(data.get("cells", []), key=lambda cell: (cell["x"], cell["y"]))
    data["monsters"] = sorted(
        ({key: value for key, value in monster.items() if key != "id"}
         for monster in data.get("monsters", [])), key=lambda monster: (monster["x"], monster["y"], monster["name"]))
    data["inventory"] = sorted(data.get("inventory", []), key=lambda item: item["slot"])
    return data


_EXPLORATION_COUNTS = (
    "actions", "turns", "movement_actions", "revisit_movement_actions",
    "revealed_walkable_cells", "reveal_observed_actions",
    "exploration_actions", "exploration_turns", "exploration_revealed_walkable_cells",
    "exploration_reveal_observed_actions", "exploration_reveal_observed_turns",
    "no_reveal_exploration_actions", "no_reveal_exploration_turns",
)


class _ExplorationEvidence:
    """Keep coordinate sets, never action histories or repeated map snapshots.

    Arrival means first *observed* arrival. Positions are observed endpoints,
    not an inferred path through multi-turn actions. Walkable terrain uses the
    public Cell policy, including remembered cells, not the unknown full map.
    Costs and action-attributed reveals belong to the action's starting floor;
    known terrain and visited positions belong to their observed location.
    """

    def __init__(self):
        self.locations = {}
        self.totals = Counter()
        self.objectives = defaultdict(Counter)
        self.missing = Counter()
        self.last_observed_turn = None

    @staticmethod
    def _location(observation):
        place, depth = observation.get("place"), observation.get("depth")
        return f"{place}:{depth}" if place and isinstance(depth, int) else None

    @staticmethod
    def _position(observation):
        position = observation.get("position")
        return tuple(position) if isinstance(position, (list, tuple)) and len(position) == 2 else None

    def observe(self, observation):
        turn = observation.get("turn")
        if isinstance(turn, int):
            self.last_observed_turn = turn
        location = self._location(observation)
        if location is None:
            self.missing["location_observations"] += 1
            return None, None
        floor = self.locations.setdefault(location, {
            "first_arrival_turn": turn, "first_descent_turn": None,
            "known": set(), "visited": set(), "counts": Counter(),
            "objectives": defaultdict(Counter),
        })
        position = self._position(observation)
        if position is not None:
            floor["visited"].add(position)
        else:
            self.missing["position_observations"] += 1
        cells = observation.get("cells")
        if not isinstance(cells, list):
            self.missing["map_observations"] += 1
            return floor, None
        previous = len(floor["known"])
        floor["known"].update(
            (cell["x"], cell["y"]) for cell in cells
            if Cell(cell["x"], cell["y"], cell.get("feature", 0)).walkable
        )
        return floor, len(floor["known"]) - previous

    def record(self, before, action, after):
        floor, before_reveal = self.observe(before)
        before_location = self._location(before)
        after_location = self._location(after)
        before_position = self._position(before)
        after_position = self._position(after)
        # An unchanged position is a bump attack/door/wait, not a revisit.
        movement = (action["kind"] in {"move", "explore"} and floor is not None
                    and before_location == after_location
                    and before_position is not None and after_position is not None
                    and before_position != after_position)
        revisit = movement and after_position in floor["visited"]
        _, revealed = self.observe(after)
        turns = (max(0, after["turn"] - before["turn"])
                 if isinstance(before.get("turn"), int) and isinstance(after.get("turn"), int)
                 else 0)
        if not isinstance(before.get("turn"), int) or not isinstance(after.get("turn"), int):
            self.missing["turn_delta_actions"] += 1
        objective = action.get("objective", "")
        exploring = objective == "explore" or action["kind"] == "explore"
        reveal_observed = before_reveal is not None and revealed is not None
        counts = Counter(actions=1, turns=turns, movement_actions=int(movement),
                         revisit_movement_actions=int(revisit),
                         reveal_observed_actions=int(reveal_observed),
                         revealed_walkable_cells=revealed if reveal_observed else 0)
        if exploring:
            counts.update(exploration_actions=1, exploration_turns=turns)
            if reveal_observed:
                counts.update(exploration_reveal_observed_actions=1,
                              exploration_reveal_observed_turns=turns,
                              exploration_revealed_walkable_cells=revealed)
                if not revealed:
                    counts.update(no_reveal_exploration_actions=1,
                                  no_reveal_exploration_turns=turns)
        self.totals.update(counts)
        self.objectives[objective].update(actions=1, turns=turns)
        if floor is not None:
            floor["counts"].update(counts)
            floor["objectives"][objective].update(actions=1, turns=turns)
            if (action["kind"] == "stairs_down" and after_location is not None
                    and before_location != after_location and floor["first_descent_turn"] is None):
                floor["first_descent_turn"] = after.get("turn")

    def result(self):
        locations = {}
        for location, floor in self.locations.items():
            locations[location] = {
                "first_arrival_turn": floor["first_arrival_turn"],
                "first_descent_turn": floor["first_descent_turn"],
                "known_walkable_cells": len(floor["known"]),
                "distinct_positions_visited": len(floor["visited"]),
                **{key: floor["counts"][key] for key in _EXPLORATION_COUNTS},
                "objective_costs": dict(floor["objectives"]),
            }
        return {
            "schema_version": 1, "last_observed_turn": self.last_observed_turn,
            "totals": {
                **{key: self.totals[key] for key in _EXPLORATION_COUNTS},
                "known_walkable_cells": sum(len(floor["known"]) for floor in self.locations.values()),
                "distinct_positions_visited": sum(len(floor["visited"]) for floor in self.locations.values()),
            },
            "objective_costs": dict(self.objectives), "locations": locations,
            "missing_data": {key: self.missing[key] for key in (
                "location_observations", "position_observations", "map_observations", "turn_delta_actions")},
        }


def run_evidence(directory: Path, character: CharacterProfile) -> dict:
    """Stream logs; do not load full maps for every decision into memory."""
    initial = None
    trajectory = hashlib.sha256()
    meaningful = 0
    objectives = Counter()
    equipment = []
    exploration = _ExplorationEvidence()
    with (directory / "events.jsonl").open() as stream:
        for line in stream:
            event = json.loads(line)
            if event["kind"] == "character_ready":
                initial = event["data"]["observation"]
                break
    if initial is not None:
        exploration.observe(initial)
    with (directory / "actions.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            if initial is None:
                initial = row["before"]
            action = row["action"]
            exploration.record(row["before"], action, row["after"])
            trajectory.update((_digest({"action": [action["kind"], action["value"]],
                                        "after": observable_state(row["after"])}) + "\n").encode())
            meaningful += row["outcome"]["turn_delta"] > 0
            objectives[action["objective"]] += 1
            if action["kind"] in {"pickup", "wield", "wear"}:
                equipment.append({"turn": row["before"]["turn"], "action": action,
                                  "inventory_changed": row["before"]["inventory"] != row["after"]["inventory"]})
    seeds = {int(match.group(1)) for message in (initial or {}).get("messages", [])
             if (match := re.search(r"\bGame seed: (\d+) \(custom seed\)", message))}
    return {"initial_state_sha256": _digest(observable_state(initial)) if initial else None,
            "trajectory_sha256": trajectory.hexdigest(), "meaningful_actions": meaningful,
            "initial_turn": initial["turn"] if initial else None,
            "engine_seed": next(iter(seeds)) if len(seeds) == 1 else None,
            "character": character.metadata(),
            "character_verified": bool(initial and character.matches_start(Observation(
                species=initial.get("species", ""), god=initial.get("god", ""),
                messages=tuple(initial.get("messages", []))))),
            "objectives": dict(objectives), "equipment_actions": equipment,
            "exploration": exploration.result()}


def _distribution(values):
    return {"count": len(values), "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None}


def _aggregate_exploration(trials, locations):
    available = [row for row in trials if isinstance(row.get("exploration"), dict)]
    totals = Counter()
    objectives = defaultdict(Counter)
    missing = Counter()
    for row in available:
        evidence = row["exploration"]
        totals.update(evidence.get("totals", {}))
        missing.update(evidence.get("missing_data", {}))
        for objective, costs in evidence.get("objective_costs", {}).items():
            objectives[objective].update(costs)
    floors = {}
    for location in sorted(locations):
        arrivals, descents = [], []
        absent = Counter()
        floor_totals = Counter()
        floor_objectives = defaultdict(Counter)
        for row in available:
            floor = row["exploration"].get("locations", {}).get(location)
            if floor is None:
                absent[row["outcome"]] += 1
                continue
            if floor.get("first_arrival_turn") is not None:
                arrivals.append(floor["first_arrival_turn"])
            if floor.get("first_descent_turn") is not None:
                descents.append(floor["first_descent_turn"])
            floor_totals.update({key: floor.get(key, 0) for key in (
                *_EXPLORATION_COUNTS, "known_walkable_cells", "distinct_positions_visited")})
            for objective, costs in floor.get("objective_costs", {}).items():
                floor_objectives[objective].update(costs)
        floors[location] = {
            "eligible_runs": len(available), "observed_runs": len(available) - sum(absent.values()),
            "not_observed_runs": sum(absent.values()), "not_observed_by_outcome": dict(absent),
            "first_arrival_turn": _distribution(arrivals),
            "first_descent_turn": _distribution(descents),
            "totals": dict(floor_totals),
            "objective_costs": dict(floor_objectives),
        }
    movement = totals["movement_actions"]
    revealed = totals["exploration_revealed_walkable_cells"]
    return {
        "eligible_runs": len(trials), "runs_with_metrics": len(available),
        "runs_missing_metrics": len(trials) - len(available),
        "totals": dict(totals), "objective_costs": dict(objectives), "locations": floors,
        "missing_data": dict(missing),
        "revisit_fraction_of_movement_actions": totals["revisit_movement_actions"] / movement if movement else None,
        "exploration_turns_per_revealed_walkable_cell":
            totals["exploration_reveal_observed_turns"] / revealed if revealed else None,
    }


def aggregate(rows: list[dict]) -> dict:
    # Old recordings retain their descriptive metrics, but unknown character
    # identity is not evidence of a matched starting combo.
    known = [row for row in rows if isinstance(row.get("character"), dict)
             and row["character"].get("id")
             and {"species", "background", "god"} <= row["character"].keys()]
    identities = {_digest({"character": row["character"], "module": row.get("character_module")})
                  for row in known}
    if len(identities) > 1 or (known and len(known) != len(rows)):
        raise ValueError("Cannot compare evaluation rows with different or unknown character profiles")
    character = known[0]["character"] if known else None
    character_module = known[0].get("character_module") if known else None
    agents = {}
    by_agent = defaultdict(list)
    repeats = defaultdict(list)
    paired = defaultdict(list)
    locations = {location for row in rows if row["valid_start"]
                 for location in (row.get("exploration") or {}).get("locations", {})}
    for row in rows:
        by_agent[row["agent"]].append(row)
        repeats[(row["agent"], row["seed"])].append(row)
        paired[(row["seed"], row["repeat"])].append(row)
    for agent, trials in by_agent.items():
        valid = [row for row in trials if row["valid_start"]]
        counts = Counter(row["outcome"] for row in trials)
        agents[agent] = {
            "runs": len(trials), "valid_starts": len(valid), "outcomes": dict(counts),
            "deaths": sum(row["dead"] for row in valid),
            "turn_limit_censored": counts["turn_limit"],
            "mean_observed_turns": statistics.mean(row["turns"] for row in valid) if valid else None,
            "median_observed_turns": statistics.median(row["turns"] for row in valid) if valid else None,
            "deepest_depth": max((row["deepest_depth"] for row in valid), default=None),
            "max_xl": max((row["max_xl"] for row in valid), default=None),
            "kills": sum(row["kills"] for row in valid),
            "failure_reasons": dict(Counter(row["reason"] for row in trials if row["outcome"] in {"paused", "error", "timeout"})),
            "exploration": _aggregate_exploration(valid, locations),
        }
    repeatability = []
    for (agent, seed), trials in repeats.items():
        if len(trials) < 2:
            continue
        valid = character is not None and all(row["valid_start"] for row in trials)
        repeatability.append({"agent": agent, "seed": seed, "trials": len(trials),
                              "same_initial_state": valid and len({row["initial_state_sha256"] for row in trials}) == 1,
                              "same_observable_trace": valid and len({(row["trajectory_sha256"], row["outcome"]) for row in trials}) == 1,
                              "same_rc": valid and len({row["rc_sha256"] for row in trials}) == 1})
    comparisons = []
    for (seed, repeat), trials in paired.items():
        if len(trials) < 2:
            continue
        comparable = (character is not None and all(row["valid_start"] for row in trials)
                      and len({row["initial_state_sha256"] for row in trials}) == 1
                      and len({row["rc_sha256"] for row in trials}) == 1)
        comparisons.append({"seed": seed, "repeat": repeat, "comparable_start": comparable,
                            "agents": {row["agent"]: {
                                **{key: row[key] for key in ("outcome", "turns", "dead", "deepest_depth", "kills")},
                                "exploration": row.get("exploration"),
                            } for row in trials}})
    return {"character": character, "character_module": character_module,
            "agents": agents, "repeatability": repeatability, "paired_comparisons": comparisons,
            "interpretation": (
                "Observed turns are capped/censored, not wins or expected lifespan. Pauses/errors are reported separately. "
                "Matching hashes cover public state/action traces only. Exploration aggregates include all valid-start "
                "outcomes with metrics, never only survivors. Arrival/descent means condition on observed events, with "
                "counts; not-observed outcomes distinguish deaths from censored/paused runs and do not imply zero turns. "
                "Locations are the union observed across valid runs, not a list of all possible floors. "
                "First arrival is first observed arrival, potentially left-censored in partial recordings. "
                "Distinct positions are observed endpoints only; multi-turn paths are not reconstructed. "
                "Walkable cells are cumulative public terrain under Cell.walkable, not total map size or map completion. "
                "Actions/turns/reveals are attributed to their starting floor; initial and before-only map gains are "
                "included in known terrain, not action reveal gains. Revisit fractions divide by position-changing "
                "move/explore actions within one location, excluding waits, bump attacks and stairs. "
                "Exploration means objective=explore or kind=explore; missing maps are not no-reveal observations.")}


async def run_evaluation(base: Config, plan: Evaluation, output: Path | None = None) -> dict:
    plan.validate()
    character = get_character(base.character)
    character_module = BUILTIN_CHARACTERS.get(base.character.casefold(), base.character)
    source = inspect.getsourcefile(importlib.import_module(character_module.rsplit(":", 1)[0]))
    selection = {"character": character.metadata(), "character_requested": base.character,
                 "character_module": character_module,
                 "character_source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest() if source else None}
    # Validate plugins before creating accounts; each actual run gets a fresh instance.
    for identifier in plan.agents:
        make_agent(identifier, character)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = (output or base.run_root / "evaluations") / batch_id
    directory.mkdir(parents=True, exist_ok=False)
    private = base.control_socket.parent / ("eval-" + batch_id.rsplit("-", 1)[1])
    private.mkdir(parents=True, mode=0o700, exist_ok=False)
    socket = private / "control.sock"
    package = Path(__file__).parent
    sources = {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(package.rglob("*.py"))}
    manifest = {**selection, "batch_id": batch_id, "engine": {"version": DCSS_VERSION, "commit": DCSS_COMMIT},
                "engine_identity": "Native reported release checked; exact commit requires the documented pinned server",
                "seeds": list(plan.seeds), "agents": list(plan.agents), "repeats": plan.repeats,
                "max_turns": plan.max_turns, "max_actions": plan.max_actions,
                "seconds_per_run": plan.seconds_per_run, "delay": plan.delay, "record_video": plan.record_video,
                "control_socket": str(socket), "package_sources_sha256": sources,
                "pregen_dungeon": "incremental", "fresh_accounts": True}
    _json(directory / "manifest.json", manifest)
    stopped = asyncio.Event()
    current = None
    def stop():
        stopped.set()
        if current is not None:
            current.request_stop()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    rows = []
    expected = len(plan.seeds) * len(plan.agents) * plan.repeats
    try:
        for seed in plan.seeds:
            for repeat in range(1, plan.repeats + 1):
                for identifier in plan.agents:
                    if stopped.is_set():
                        break
                    username = "Eval" + uuid4().hex[:16]
                    config = replace(base, username=username, password="", seed=seed,
                                     game_id=base.seeded_game_id,
                                     delay=plan.delay, max_actions=plan.max_actions, max_turns=plan.max_turns,
                                     control_socket=socket, run_root=directory / "runs", agent=identifier,
                                     record_video=plan.record_video, paused=False)
                    config.validate()
                    config.ensure_credentials()
                    agent = make_agent(identifier, character)
                    source = inspect.getsourcefile(type(agent))
                    metadata = config.public_dict()
                    metadata.update(selection)
                    metadata["evaluation"] = {"batch_id": batch_id, "seed": seed, "repeat": repeat,
                                              "agent_identifier": getattr(agent, "identifier", identifier),
                                              "agent_source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest() if source else None}
                    recorder = Recorder(config.run_root, metadata)
                    def event(kind, data):
                        recorder.event(kind, data)
                        if kind == "engine_version" and not expected_version(data["text"]):
                            raise ProtocolError(f"Evaluation requires DCSS {DCSS_VERSION}, received {data['text']}")
                    adapter = WebTilesAdapter(config.url, config.username, config.password, config.game_id,
                                              config.timeout, event, seed=seed, character=character)
                    current = Supervisor(config, agent, adapter, recorder)
                    print("EVAL_RUN " + json.dumps({"index": len(rows) + 1, "total": expected, "seed": seed,
                                                    "agent": identifier, **selection, "username": username, "run_dir": str(recorder.path),
                                                    "control_socket": str(socket)}), flush=True)
                    timed_out = False
                    with (recorder.path / "console.log").open("w") as console, redirect_stdout(console):
                        task = asyncio.create_task(current.run(stop_on_pause=True))
                        done, _ = await asyncio.wait({task}, timeout=plan.seconds_per_run)
                        if not done:
                            timed_out = True
                            recorder.event("evaluation_timeout", {"seconds": plan.seconds_per_run})
                            current.request_stop()
                        result = await task
                    current = None
                    if result["result"] == "stopped" and not timed_out:
                        stopped.set()
                    if adapter.seed_rc is not None:
                        (recorder.path / "game.rc").write_text(adapter.seed_rc)
                    evidence = run_evidence(recorder.path, character)
                    valid = (adapter.seed_confirmed and evidence["initial_turn"] == 0
                             and evidence["character_verified"] and evidence["engine_seed"] == seed
                             and expected_version(adapter.engine_version))
                    outcome = "timeout" if timed_out else result["result"]
                    if not valid:
                        outcome = "error"
                    reason = (f"Wall-clock deadline reached ({plan.seconds_per_run:g}s)" if timed_out else
                              result["details"].get("pause_reason") or result["details"].get("error")
                              or (f"No verified fresh seeded {character.species} {character.background} start "
                                  f"for {base.character!r}" if not valid else ""))
                    row = {**selection, "seed": seed, "repeat": repeat, "agent": identifier, "username": username,
                           "run_dir": str(recorder.path), "outcome": outcome, "reason": reason,
                           "valid_start": valid, "seed_confirmation": "rc_readback_and_game_banner" if valid else None,
                           "rc_sha256": adapter.seed_rc_sha256, "turns": result["turns_survived"],
                           "engine_version": adapter.engine_version,
                           "dead": result["dead"], "deepest_depth": result["deepest_depth"], "max_xl": result["max_xl"],
                           "kills": result["kills"], "death_cause": result["death_cause"],
                           "retreats": result["retreats"], "emergencies": result["emergencies"],
                           "consumables_used": result["consumables_used"], "recording": result["details"].get("video"),
                           **evidence}
                    rows.append(row)
                    _json(recorder.path / "evaluation.json", row)
                    with (directory / "results.jsonl").open("a") as stream:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    report = {"batch_id": batch_id, "completed_runs": len(rows), "planned_runs": expected,
                              "interrupted": stopped.is_set(), **aggregate(rows), **selection}
                    _json(directory / "report.json", report)
                    print("EVAL_RESULT " + json.dumps({key: row[key] for key in (
                        "seed", "repeat", "agent", "character_requested", "character", "character_module",
                        "outcome", "turns", "kills", "reason")}), flush=True)
                if stopped.is_set():
                    break
            if stopped.is_set():
                break
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        report = {"batch_id": batch_id, "directory": str(directory), "completed_runs": len(rows),
                  "planned_runs": expected, "interrupted": stopped.is_set(), **aggregate(rows), **selection}
        _json(directory / "report.json", report)
    failures = any(row["outcome"] in {"error", "timeout", "paused"} for row in rows)
    mismatches = any(not item["same_initial_state"] or not item["same_rc"] for item in report["repeatability"])
    mismatches |= any(not item["comparable_start"] for item in report["paired_comparisons"])
    return {**report, "error": failures or mismatches or stopped.is_set() or len(rows) != expected}
