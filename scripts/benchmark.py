#!/usr/bin/env python3
"""Frozen-source bounded trials and portable public-trace survival reports.

launch snapshots a package before starting isolated one-seed evaluation subprocesses.
report also ingests independent existing evaluation batches, including gzip logs.
Nothing reads game saves. Reports describe observations, not causal attribution.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, deque
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import sys
import tempfile

DEVELOPMENT_SEEDS = tuple(range(1, 9))
HELDOUT_SEEDS = tuple(range(101, 131))
PASSIVE = {"plant", "demonic plant", "withered plant", "fungus", "toadstool", "bush", "butterfly"}
EMERGENCY_ITEM = re.compile(r"healing|curing|haste|heal wounds|teleportation|blinking|fear|butterflies", re.I)
VICTORY = re.compile(r"escaped with the Orb|You (?:have )?won the game|escaped.*Orb of Zot", re.I)
LEGACY_OBSERVATION_FIELDS = (
    "species", "god", "turn", "time", "place", "depth", "hp", "hp_max", "xl",
    "position", "statuses", "inventory", "monsters", "cells", "dead", "game_over")


def initial_fingerprint(observation: dict | None) -> str | None:
    """Compare the complete original public schema, never fabricate missing fields."""
    if not observation or not all(key in observation for key in LEGACY_OBSERVATION_FIELDS):
        return None
    selected = {key: observation[key] for key in LEGACY_OBSERVATION_FIELDS}
    for name, keys, sort_key in (
        ("inventory", ("slot", "letter", "name", "quantity", "equipped"), lambda item: item["slot"]),
        ("monsters", ("name", "x", "y", "threat", "attitude"), lambda item: (item["x"], item["y"], item["name"])),
        ("cells", ("x", "y", "feature", "glyph", "visible", "cloud"), lambda item: (item["x"], item["y"])),
    ):
        if any(not all(key in item for key in keys) for item in observation[name]):
            return None
        selected[name] = sorted(({key: item[key] for key in keys} for item in observation[name]), key=sort_key)
    return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def jsonl(path: Path):
    """Prefer original if both forms exist; malformed/truncated input is an error."""
    if not path.exists():
        path = path.with_suffix(path.suffix + ".gz")
    if not path.exists():
        return
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"Malformed JSONL {path}:{number}: {exc}") from exc


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> dict:
    if not 0 <= successes <= n:
        raise ValueError("Wilson successes must be between zero and n")
    if not n:
        return {"n": 0, "count": 0, "proportion": None, "ci95": [None, None]}
    p = successes / n
    scale = 1 + z * z / n
    center = (p + z * z / (2 * n)) / scale
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / scale
    return {"n": n, "count": successes, "proportion": p,
            "ci95": [max(0, center - half), min(1, center + half)]}


def distribution(values) -> dict:
    values = list(values)
    return {"n": len(values), "median": statistics.median(values) if values else None,
            "max": max(values, default=None)}


def hostiles(obs: dict) -> list:
    return [m for m in obs.get("monsters", [])
            if m.get("attitude", 0) == 0 and m.get("name", "").lower() not in PASSIVE]


def emergency_inventory(obs: dict, *, unidentified: bool = False) -> list:
    return [{k: item.get(k) for k in ("letter", "name", "quantity")}
            for item in obs.get("inventory", [])
            if item.get("quantity", 1) > 0 and ("{unknown}" in item.get("name", "").lower()) == unidentified
            and EMERGENCY_ITEM.search(item.get("name", ""))]


def compact_step(row: dict) -> dict:
    before, after = row.get("before", {}), row.get("after", {})
    return {"sequence": row.get("sequence"), "turn": before.get("turn"),
            "location": f"{before.get('place', '')}:{before.get('depth', '')}",
            "hp_before": before.get("hp"), "hp_after": after.get("hp"),
            "hp_max": before.get("hp_max"), "position": before.get("position"),
            "action": row.get("action"), "danger_reasons": row.get("danger_reasons", []),
            "hostiles": [{k: m.get(k) for k in ("name", "x", "y", "threat")} for m in hostiles(before)],
            "statuses": before.get("statuses", []),
            "messages": row.get("outcome", {}).get("new_messages", []),
            "available_emergencies": emergency_inventory(before)}


def run_report(row: dict, directory: Path, manifest: dict, annotation: dict | None = None) -> dict:
    summary = read_json(directory / "summary.json", {})
    final = summary.get("final_observation", {})
    outcome = row.get("outcome", summary.get("result", "incomplete"))
    milestones = {name: None for name in ("D5", "D10", "Lair", "Lair_clear")}
    dungeon_depths, xls = [], []
    death_messages, win_messages = [], []
    consumed = Counter()
    dangerous_drops = []
    encounters = []
    current_encounter = None
    retreats = []
    current_retreat = None
    context = deque(maxlen=4)
    first_flag = None
    flag_window = []
    flag_after = 0
    tail = deque(maxlen=8)
    last_obs = final
    seen_observations = 0
    actions = 0
    initial_observation = None
    stalled_actions = 0
    flags = Counter()
    trace_exists = (directory / "actions.jsonl").exists() or (directory / "actions.jsonl.gz").exists()

    def observe(obs):
        nonlocal seen_observations
        if not obs:
            return
        seen_observations += 1
        depth, place = obs.get("depth"), obs.get("place")
        if place in {"D", "Dungeon"} and isinstance(depth, (int, float)):
            dungeon_depths.append(depth)
            for target in (5, 10):
                if depth >= target and milestones[f"D{target}"] is None:
                    milestones[f"D{target}"] = {"turn": obs.get("turn"), "location": f"D:{depth}"}
        if place == "Lair" and milestones["Lair"] is None:
            milestones["Lair"] = {"turn": obs.get("turn"), "location": f"Lair:{depth}"}
        if isinstance(obs.get("xl"), (int, float)):
            xls.append(obs["xl"])
        for message in obs.get("messages", []):
            if re.search(r"You die|slain by|killed by|mangled by|demolished by|succumbed to", message, re.I):
                if message not in death_messages:
                    death_messages.append(message)
            if VICTORY.search(message) and message not in win_messages:
                win_messages.append(message)

    for action in jsonl(directory / "actions.jsonl"):
        actions += 1
        before, after = action.get("before", {}), action.get("after", {})
        if initial_observation is None:
            initial_observation = before
        observe(before)
        observe(after)
        last_obs = after
        step = compact_step(action)
        tail.append(step)
        enemies = hostiles(before)
        if enemies:
            if current_encounter is None:
                current_encounter = {"first_sequence": step["sequence"], "first_turn": before.get("turn"),
                                     "location": step["location"], "monsters": set()}
            current_encounter["monsters"].update(m.get("name", "unknown") for m in enemies)
            current_encounter["last_sequence"] = step["sequence"]
            current_encounter["last_turn"] = after.get("turn")
        elif current_encounter is not None:
            current_encounter["monsters"] = sorted(current_encounter["monsters"])
            encounters.append(current_encounter)
            current_encounter = None
        act = action.get("action", {})
        objective = act.get("objective", "").lower()
        retreating = any(word in objective for word in ("retreat", "escape", "flee"))
        if retreating and current_retreat is None:
            current_retreat = {"first_sequence": step["sequence"], "first_turn": before.get("turn"),
                               "initial_hp": before.get("hp"), "hostiles_initial": len(enemies),
                               "start_location": step["location"], "actions": 0}
        if current_retreat is not None:
            current_retreat.update(last_sequence=step["sequence"], last_turn=after.get("turn"),
                                   final_hp=after.get("hp"), hostiles_final=len(hostiles(after)))
            current_retreat["actions"] += 1
            if not hostiles(after) and not after.get("dead"):
                current_retreat["escape_evidence"] = "observed_no_visible_hostiles; not proof destination safe"
                retreats.append(current_retreat)
                current_retreat = None
            elif not retreating or after.get("dead"):
                current_retreat["escape_evidence"] = "not_observed"
                retreats.append(current_retreat)
                current_retreat = None
        used = action.get("outcome", {}).get("consumables_used", {})
        consumed.update(used)
        hp_max = before.get("hp_max", 0)
        prior_hp = before.get("hp", 0)
        before_berserk = any(str(status).casefold() == "berserk" for status in before.get("statuses", []))
        after_berserk = any(str(status).casefold() == "berserk" for status in after.get("statuses", []))
        if before_berserk != after_berserk and hp_max and after.get("hp_max", 0):
            # Native rage rescales HP. Count observed loss on the new scale,
            # not the removal of temporary HP as incoming damage. Draining
            # without a Berserk transition must remain visible.
            prior_hp = prior_hp * after["hp_max"] // hp_max
            hp_max = after["hp_max"]
        drop = max(0, prior_hp - after.get("hp", 0))
        if hp_max and drop >= max(5, hp_max * .2):
            dangerous_drops.append({"sequence": step["sequence"], "turn": step["turn"],
                                    "hp_before": before.get("hp"), "hp_after": after.get("hp"),
                                    "hp_max": hp_max, "observed_loss": drop,
                                    "interpretation": "net observed loss, Berserk scale-normalized; regeneration can mask damage",
                                    "new_messages": step["messages"]})
        if action.get("outcome", {}).get("state_changed") is False:
            stalled_actions += 1
        candidates = []
        if enemies and hp_max and before.get("hp", 0) <= .35 * hp_max and not retreating and not used:
            candidates.append("low_hp_exposed_without_observed_escape_or_consumption")
        if enemies and act.get("kind") in {"wait", "explore", "pickup", "wear"}:
            candidates.append("noncombat_action_with_visible_hostiles")
        if enemies and act.get("kind") in {"stairs_up", "stairs_down", "escape_upstairs"}:
            candidates.append("stairs_with_visible_hostiles")
        if action.get("emergency") and emergency_inventory(before) and not used and not retreating:
            candidates.append("emergency_with_unused_identified_emergency_items")
        flags.update(candidates)
        if candidates and first_flag is None:
            first_flag = {"sequence": step["sequence"], "turn": step["turn"], "flags": candidates,
                          "status": "HEURISTIC FLAGS; not proven earliest avoidable mistake or causal attribution"}
            flag_window = list(context) + [step]
            flag_after = 3
        elif flag_after:
            flag_window.append(step)
            flag_after -= 1
        context.append(step)
    observe(final)
    if current_encounter:
        current_encounter["monsters"] = sorted(current_encounter["monsters"])
        encounters.append(current_encounter)
    if current_retreat:
        current_retreat["escape_evidence"] = "not_observed_before_recording_end"
        retreats.append(current_retreat)
    dead = bool(summary.get("dead", row.get("dead", False)) or last_obs.get("dead"))
    game_over = bool(summary.get("game_over") or last_obs.get("game_over"))
    end_reason = summary.get("details", {}).get("end_reason", "")
    if end_reason == "won" or VICTORY.search(end_reason):
        win_messages.append(end_reason)
    won = game_over and not dead and bool(win_messages)
    # A generic game_over includes quitting; never turn it into victory.
    status = "death" if dead else "win" if won else outcome
    if status in {"dead", "won", "win", "game_over"} and not dead and not won:
        status = "unclassified_game_over" if game_over else "incomplete"
    event_counts = Counter()
    failure_categories = Counter()
    errors = []
    for event in jsonl(directory / "events.jsonl"):
        kind, data = event.get("kind", ""), event.get("data", {})
        if kind == "character_ready":
            initial_observation = data.get("observation")
        # Count execution failures, not words in monster descriptions, strategy
        # explanations or embedded observations. Avoid re-encoding every map.
        text = " ".join((kind, *(str(data.get(key, "")) for key in ("error", "type", "reason")))).lower()
        for category, pattern in (("stalls", r"stall|no (?:meaningful )?progress"),
                                  ("invalid_actions", r"invalid action|invalidaction"),
                                  ("protocol_errors", r"protocolerror|protocol error"),
                                  ("errors", r"error|uncertain")):
            if re.search(pattern, text):
                failure_categories[category] += 1
        if any(word in kind.lower() for word in ("error", "stall", "invalid", "reconnect", "pause", "timeout", "uncertain")):
            event_counts[kind] += 1
            if len(errors) < 20:
                errors.append({"sequence": event.get("sequence"), "kind": kind, "data": data})
    annotation = annotation or {}
    clear = annotation.get("Lair_clear")
    if isinstance(clear, dict) and clear.get("reviewed") is True and clear.get("evidence"):
        milestones["Lair_clear"] = {"human_reviewed": True, **clear}
    return {"seed": row["seed"], "repeat": row.get("repeat", 1), "agent": row.get("agent", "rule"),
            "valid_start": row.get("valid_start", False), "outcome": status,
            "recorded_outcome": outcome, "death": dead, "win": won,
            "game_over_observed": game_over, "win_evidence": win_messages,
            "censored": not dead and not won, "turns": row.get("turns", summary.get("turns_survived")),
            "reason": row.get("reason", ""), "max_dungeon_depth": max(dungeon_depths, default=None),
            "max_xl": max(xls, default=row.get("max_xl")), "milestones": milestones,
            "milestone_status": {key: "observed" if value else
                "unknown_requires_review" if key == "Lair_clear" else
                "not_observed_before_death" if dead else "not_observed_before_censoring"
                for key, value in milestones.items()},
            "death_cause": summary.get("death_cause", row.get("death_cause")),
            "death_cause_source": summary.get("death_cause_source"), "death_messages": death_messages[-8:],
            "consumables_used": dict(consumed) if trace_exists else row.get("consumables_used", {}),
            "unused_emergencies_at_end": emergency_inventory(final or last_obs),
            "unidentified_emergencies_revealed_at_death": emergency_inventory(final or last_obs,unidentified=True) if dead else [],
            "dangerous_hp_drops": dangerous_drops, "retreat_episodes": retreats,
            "encounters": encounters if dead else [], "heuristic_flag_counts": dict(flags),
            "death_review": {"earliest_flag_candidate": first_flag, "chronological_window": flag_window,
                             "final_window": list(tail), "human_review": annotation.get("death_review")}
                            if dead else None,
            "protocol_events": dict(event_counts), "protocol_evidence": errors,
            "failure_categories": dict(failure_categories),
            "unchanged_state_actions": stalled_actions,
            "trace_available": trace_exists, "observations_inspected": seen_observations, "actions_inspected": actions,
            "pairing": {key: row.get(key, manifest.get(key)) for key in
                        ("initial_state_sha256", "rc_sha256", "character", "character_module", "engine_version")},
            "initial_common_schema_sha256": initial_fingerprint(initial_observation),
            "initial_fingerprint_schema": list(LEGACY_OBSERVATION_FIELDS),
            "initial_additional_public_fields": {
                key: value for key, value in (initial_observation or {}).items()
                if key not in LEGACY_OBSERVATION_FIELDS
                and key not in {"name", "messages", "menu", "prompt", "input_mode"}},
            "bounds": {key: manifest.get(key) for key in ("max_turns", "max_actions", "seconds_per_run")},
            "provenance": {"package_sources_sha256": manifest.get("package_sources_sha256"),
                           "engine": manifest.get("engine"), "fresh_accounts": manifest.get("fresh_accounts")}}


def resolve_run(row: dict, batch: Path) -> Path:
    recorded = Path(row.get("run_dir", ""))
    options = [recorded, batch / recorded, batch / "runs" / recorded.name]
    for option in options:
        if option.is_dir() and (option / "summary.json").exists():
            return option.resolve()
    raise ValueError(f"Missing completed run directory for seed {row.get('seed')} in {batch}")


def collect(roots: list[Path], label: str, annotations: dict, seeds: set[int] | None = None) -> list[dict]:
    rows, visited = [], set()
    for root in roots:
        if root.is_file() and root.name in {"results.jsonl", "results.jsonl.gz"}:
            result_files = [root]
        elif root.is_dir():
            result_files = sorted(root.rglob("results.jsonl")) + sorted(root.rglob("results.jsonl.gz"))
        else:
            raise ValueError(f"Not an evaluation root/results file: {root}")
        for path in result_files:
            canonical = path.with_suffix("") if path.suffix == ".gz" else path
            if canonical.resolve() in visited:
                continue
            visited.add(canonical.resolve())
            manifest = read_json(path.parent / "manifest.json", {})
            for row in jsonl(canonical):
                if seeds is not None and row["seed"] not in seeds:
                    continue
                key = f"{label}:{row['seed']}:{row.get('repeat', 1)}"
                rows.append(run_report(row, resolve_run(row, path.parent), manifest, annotations.get(key)))
    if not rows:
        raise ValueError(f"No completed evaluation rows for {label}")
    return sorted(rows, key=lambda row: (row["seed"], row["repeat"], row["agent"]))


def aggregate(rows: list[dict]) -> dict:
    valid = [row for row in rows if row["valid_start"]]
    n = len(valid)
    milestone_stats = {}
    for name in ("D5", "D10", "Lair", "Lair_clear"):
        observed = sum(row["milestones"][name] is not None for row in valid)
        milestone_stats[name] = {**wilson(observed, n),
                                "statuses": dict(Counter(row["milestone_status"][name] for row in valid)),
                                "interpretation": "observed fraction of valid starts, not eventual success probability"}
    return {"n": len(rows), "valid_starts": n, "outcomes": dict(Counter(row["outcome"] for row in rows)),
            "wins": wilson(sum(row["win"] for row in valid), n),
            "deaths": wilson(sum(row["death"] for row in valid), n),
            "milestones": milestone_stats,
            "max_dungeon_depth": distribution(row["max_dungeon_depth"] for row in valid if row["max_dungeon_depth"] is not None),
            "max_xl": distribution(row["max_xl"] for row in valid if row["max_xl"] is not None),
            "observed_turns": distribution(row["turns"] for row in valid if row["turns"] is not None),
            "censored_turns": distribution(row["turns"] for row in valid if row["censored"] and row["turns"] is not None),
            "dangerous_hp_drops": sum(len(row["dangerous_hp_drops"]) for row in valid),
            "retreat_episodes": sum(len(row["retreat_episodes"]) for row in valid),
            "retreats_observed_no_visible_hostiles": sum(
                episode["escape_evidence"].startswith("observed_no_visible_hostiles")
                for row in valid for episode in row["retreat_episodes"]),
            "consumables_used": dict(sum((Counter(row["consumables_used"]) for row in valid), Counter())),
            "unused_emergencies_at_death": sum(len(row["unused_emergencies_at_end"]) for row in valid if row["death"]),
            "deaths_with_identified_emergencies_unused": sum(bool(row["unused_emergencies_at_end"]) for row in valid if row["death"]),
            "unidentified_emergency_stacks_revealed_at_death": sum(len(row.get("unidentified_emergencies_revealed_at_death",[])) for row in valid if row["death"]),
            "protocol_events": dict(sum((Counter(row["protocol_events"]) for row in rows), Counter())),
            "failure_categories": dict(sum((Counter(row["failure_categories"]) for row in rows), Counter())),
            "unchanged_state_actions": sum(row["unchanged_state_actions"] for row in rows)}


def paired(baseline: list[dict], improved: list[dict]) -> dict:
    def index(rows):
        result = {}
        for row in rows:
            key = (row["seed"], row["repeat"])
            if key in result:
                raise ValueError(f"Duplicate seed/repeat {key}; select exactly one strategy per report side")
            result[key] = row
        return result
    left, right = index(baseline), index(improved)
    mismatches, matches, raw_hash_differences = [], [], []
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        reasons = []
        if a is None or b is None:
            reasons.append("missing_baseline" if a is None else "missing_improved")
        else:
            if not a["valid_start"] or not b["valid_start"]:
                reasons.append("unverified_start")
            for name in ("rc_sha256", "character", "character_module", "engine_version"):
                va, vb = a["pairing"].get(name), b["pairing"].get(name)
                if va is None or vb is None or va != vb:
                    reasons.append(name)
            common_a, common_b = a.get("initial_common_schema_sha256"), b.get("initial_common_schema_sha256")
            if common_a is None or common_b is None or common_a != common_b:
                reasons.append("initial_common_schema_sha256")
            if a["pairing"].get("initial_state_sha256") != b["pairing"].get("initial_state_sha256"):
                raw_hash_differences.append({
                    "seed": key[0], "repeat": key[1],
                    "common_schema_equal": common_a is not None and common_a == common_b,
                    "interpretation": "Raw evaluator hash differs; compare explicit common legacy schema and inspect added public fields."})
            if a["provenance"].get("engine") != b["provenance"].get("engine"):
                reasons.append("engine_provenance")
            if a["bounds"] != b["bounds"] or any(value is None for value in a["bounds"].values()):
                reasons.append("run_bounds")
        if reasons:
            mismatches.append({"seed": key[0], "repeat": key[1], "reasons": reasons})
        else:
            matches.append((a, b))
    metrics = {}
    for metric in ("D5", "D10", "Lair", "Lair_clear", "win"):
        counts = Counter()
        for a, b in matches:
            av = a["win"] if metric == "win" else a["milestones"][metric] is not None
            bv = b["win"] if metric == "win" else b["milestones"][metric] is not None
            counts["both" if av and bv else "improved_only" if bv else "baseline_only" if av else "neither_observed"] += 1
        discordant = counts["improved_only"] + counts["baseline_only"]
        exact_p = None
        if matches:
            tail = sum(math.comb(discordant,k) for k in
                       range(min(counts["improved_only"],counts["baseline_only"])+1))
            exact_p = min(1.,2*tail/(1<<discordant))
        metrics[metric] = {**{name: counts[name] for name in ("both", "improved_only", "baseline_only", "neither_observed")},
                           "paired_observed_fraction_delta": (counts["improved_only"] - counts["baseline_only"]) / len(matches) if matches else None,
                           "improved_share_of_discordant_pairs": wilson(counts["improved_only"], discordant),
                           "exact_mcnemar_two_sided_p": exact_p}
    return {"matched_n": len(matches), "matched_seeds": [a["seed"] for a, _ in matches],
            "mismatches": mismatches, "raw_initial_hash_differences": raw_hash_differences, "metrics": metrics,
            "observed_turn_delta": distribution(b["turns"] - a["turns"] for a, b in matches
                                                 if a["turns"] is not None and b["turns"] is not None),
            "interpretation": "Mismatched pairs excluded; capped turns are not lifespan or victories. Milestone deltas and exact two-sided McNemar/binomial p-values compare observed attainment under caps, not survival. P-values are unadjusted for multiple comparisons."}


def source_snapshot(source: Path, destination: Path) -> dict:
    if not (source / "__init__.py").is_file():
        raise ValueError("--source must name the dcss_bot package directory")
    before = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(source.rglob("*.py"))}
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    after = {str(path.relative_to(destination)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(destination.rglob("*.py"))}
    current = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(source.rglob("*.py"))}
    if before != after or before != current:
        raise ValueError("Source changed while freezing snapshot; no workers started")
    return after


def compress_completed(root: Path) -> None:
    """Only called on a new launch-owned seed root after its subprocess exits."""
    for summary in root.rglob("summary.json"):
        # The evaluator must finish reading its own evidence before compression.
        if not (summary.parent / "evaluation.json").exists():
            continue
        for name in ("actions", "observations", "events"):
            path = summary.parent / f"{name}.jsonl"
            if not path.exists():
                continue
            target = path.with_suffix(path.suffix + ".gz")
            with path.open("rb") as src, target.open("xb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
            path.unlink()  # lossless replacement of this launch's completed log only


async def launch(args) -> int:
    seeds = tuple(args.seeds or (DEVELOPMENT_SEEDS if args.split == "development" else HELDOUT_SEEDS))
    if len(set(seeds)) != len(seeds) or any(not 1 <= seed < 2**64 for seed in seeds):
        raise ValueError("Seeds must be distinct integers from 1 through 2^64-1")
    if not 1 <= args.concurrency <= 32 or min(args.max_turns, args.max_actions) <= 0:
        raise ValueError("Concurrency must be 1..32 and limits positive")
    if not math.isfinite(args.seconds_per_run) or args.seconds_per_run <= 0:
        raise ValueError("Per-run deadline must be finite and positive")
    if not set(args.record_seeds or []).issubset(seeds):
        raise ValueError("Representative recording seeds must belong to selected seed list")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = source_snapshot(args.source.resolve(), output / "snapshot" / "dcss_bot")
    # Freeze this orchestrator too, so its analysis semantics remain inspectable.
    shutil.copyfile(Path(__file__), output / "benchmark.py")
    private_root = output / "private"
    private_root.mkdir(mode=0o700)
    # Linux AF_UNIX paths are bounded. Keep credentials in the output tree but
    # address them through a unique short alias; never borrow an existing socket.
    socket_alias = Path(tempfile.mkdtemp(prefix="dcss-bench-", dir="/tmp")) / "state"
    socket_alias.symlink_to(private_root, target_is_directory=True)
    plan = {"label": args.label, "created_at": datetime.now(timezone.utc).isoformat(),
            "seeds": seeds, "split": args.split if not args.seeds else "explicit",
            "development_seeds": DEVELOPMENT_SEEDS, "heldout_seeds": HELDOUT_SEEDS,
            "max_turns": args.max_turns, "max_actions": args.max_actions,
            "seconds_per_run": args.seconds_per_run, "concurrency": args.concurrency,
            "record_seeds": args.record_seeds or [], "package_sources_sha256": sources,
            "character": args.character, "agent": args.agent, "url": args.url,
            "python": sys.version, "compress_completed": not args.keep_uncompressed,
            "private_state_directory": str(private_root), "socket_alias": str(socket_alias)}
    save_json(output / "plan.json", plan)
    semaphore = asyncio.Semaphore(args.concurrency)
    active = set()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def halt():
        stop.set()
        for process in tuple(active):
            try:
                process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, halt)

    async def worker(seed):
        async with semaphore:
            if stop.is_set():
                return {"seed": seed, "outcome": "not_started"}
            seed_root = output / f"seed-{seed}"
            seed_root.mkdir()
            private = socket_alias / f"seed-{seed}"
            private.mkdir(mode=0o700)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("BOT_") and key not in {"CONTROL_SOCKET", "RUN_DIR", "RECORD_VIDEO", "PYTHONPATH"}}
            env.update(PYTHONPATH=str(output / "snapshot"), DCSS_URL=args.url)
            command = [sys.executable, "-m", "dcss_bot.cli", "--control-socket", str(private / "control.sock"),
                       "evaluate", "--seeds", str(seed), "--agents", args.agent, "--character", args.character,
                       "--max-turns", str(args.max_turns), "--max-actions", str(args.max_actions),
                       "--seconds-per-run", str(args.seconds_per_run), "--delay", "0", "--explore-delay", "0",
                       "--output-root", str(seed_root / "evaluations")]
            if seed in (args.record_seeds or []):
                command.append("--record")
            with (seed_root / "launcher.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(*command, cwd=output, env=env,
                                                               stdout=log, stderr=asyncio.subprocess.STDOUT)
                active.add(process)
                if stop.is_set():
                    process.terminate()
                forced = False
                try:
                    # Evaluation requests an orderly save at its own deadline; bound a hung shutdown too.
                    code = await asyncio.wait_for(process.wait(), args.seconds_per_run + 120)
                except asyncio.TimeoutError:
                    forced = True
                    process.terminate()
                    try:
                        code = await asyncio.wait_for(process.wait(), 30)
                    except asyncio.TimeoutError:
                        process.kill()
                        code = await process.wait()
                finally:
                    active.discard(process)
            if not args.keep_uncompressed:
                await asyncio.to_thread(compress_completed, seed_root)
            result = {"seed": seed, "exit_code": code, "forced_shutdown": forced}
            save_json(seed_root / "launcher-result.json", result)
            print(json.dumps(result), flush=True)
            return result

    try:
        results = await asyncio.gather(*(worker(seed) for seed in seeds))
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        if active:
            halt()
    save_json(output / "launcher-results.json", {"runs": results, "interrupted": stop.is_set()})
    return int(any(row.get("exit_code", 1) != 0 for row in results))


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    start = commands.add_parser("launch", help="Freeze source and run bounded separate fresh-account workers")
    start.add_argument("--label", required=True)
    start.add_argument("--source", type=Path, default=Path("src/dcss_bot"))
    start.add_argument("--output", type=Path, required=True, help="New directory; existing directories are rejected")
    start.add_argument("--split", choices=("development", "heldout"), default="heldout")
    start.add_argument("--seeds", type=int, nargs="+")
    start.add_argument("--concurrency", type=int, default=4)
    start.add_argument("--max-turns", type=int, default=8000)
    start.add_argument("--max-actions", type=int, default=20000)
    start.add_argument("--seconds-per-run", type=float, default=1800)
    start.add_argument("--record-seeds", type=int, nargs="+")
    start.add_argument("--keep-uncompressed", action="store_true")
    start.add_argument("--url", default="ws://127.0.0.1:8080/socket")
    start.add_argument("--character", choices=("mibe",), default="mibe")
    start.add_argument("--agent", choices=("rule",), default="rule")
    report = commands.add_parser("report", help="Stream evaluation results and generate compact portable aggregate JSON")
    report.add_argument("--baseline", type=Path, nargs="+", required=True)
    report.add_argument("--improved", type=Path, nargs="+")
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--seeds", type=int, nargs="+", help="Select held-out or development seeds without mixing")
    report.add_argument("--annotations", type=Path, help="JSON keyed baseline:SEED:REPEAT / improved:SEED:REPEAT")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "launch":
            return asyncio.run(launch(args))
        annotations = read_json(args.annotations, {}) if args.annotations else {}
        selected = set(args.seeds) if args.seeds else None
        baseline = collect(args.baseline, "baseline", annotations, selected)
        improved = collect(args.improved, "improved", annotations, selected) if args.improved else None
        if selected:
            missing = selected - {row["seed"] for row in baseline}
            if missing:
                raise ValueError(f"Requested baseline seeds missing: {sorted(missing)}")
        result = {"schema": 2, "created_at": datetime.now(timezone.utc).isoformat(),
                  "analysis_tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "development_seeds": DEVELOPMENT_SEEDS, "heldout_seeds": HELDOUT_SEEDS,
                  "baseline": {"aggregate": aggregate(baseline), "runs": baseline},
                  "interpretation": "Public UI traces only. Censoring is not death or victory. Wilson intervals describe observed fractions, not survival estimates. Lair entry is not Lair clear; clear requires evidence-backed human review. Encounter FLAGS suggest review, not proven mistakes. Escape means no hostiles visible at an observed endpoint, never proven safety."}
        if improved is not None:
            result["improved"] = {"aggregate": aggregate(improved), "runs": improved}
            result["paired"] = paired(baseline, improved)
        save_json(args.output, result)
        print(json.dumps({key: value["aggregate"] for key, value in result.items() if key in {"baseline", "improved"}}, indent=2))
        return int(bool(result.get("paired", {}).get("mismatches")))
    except (ValueError, OSError) as exc:
        print(f"benchmark: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
