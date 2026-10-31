"""Offline evidence/report tests; never connect to a Crawl server."""
import asyncio
from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("benchmark_tool", Path(__file__).parents[1] / "scripts" / "benchmark.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


BOUNDS = {"max_turns": 8000, "max_actions": 20000, "seconds_per_run": 1800}
MANIFEST = {**BOUNDS, "engine": {"version": "0.34.1", "commit": "pinned"},
            "fresh_accounts": True, "package_sources_sha256": {"agent.py": "source"}}


def observation(**changes):
    obs = {"place": "D", "depth": 1, "turn": 0, "hp": 20, "hp_max": 20, "xl": 1,
           "position": [0, 0], "inventory": [], "monsters": [], "messages": [], "dead": False,
           "game_over": False, "statuses": [], "time": 0, "species": "Minotaur", "god": "Trog", "cells": []}
    obs.update(changes)
    return obs


def trial(**changes):
    row = {"seed": 101, "repeat": 1, "agent": "rule", "valid_start": True,
           "outcome": "turn_limit", "turns": 8000, "dead": False, "max_xl": 1,
           "initial_state_sha256": "initial", "rc_sha256": "rc", "engine_version": "DCSS 0.34.1",
           "character": {"id": "mibe", "species": "Minotaur", "background": "Berserker", "god": "Trog"},
           "character_module": "dcss_bot.characters.mibe:PROFILE"}
    row.update(changes)
    return row


def action(before, after, *, sequence=1, kind="move", objective="combat", consumed=None):
    return {"sequence": sequence, "before": before, "after": after,
            "action": {"kind": kind, "value": "l" if kind == "move" else "", "objective": objective, "reason": "test"},
            "outcome": {"new_messages": after.get("messages", []), "consumables_used": consumed or {},
                        "state_changed": before != after}, "emergency": False}


def recorded(tmp_path, rows=(), final=None, **summary_changes):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "actions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    initial = rows[0]["before"] if rows else observation()
    (directory / "events.jsonl").write_text(json.dumps(
        {"kind": "character_ready", "data": {"observation": initial}}) + "\n")
    summary = {"result": "turn_limit", "dead": False, "game_over": False,
               "final_observation": final or observation(), "turns_survived": 8000}
    summary.update(summary_changes)
    (directory / "summary.json").write_text(json.dumps(summary))
    return directory


def test_censored_milestones_are_observed_not_imputed_wins(tmp_path):
    start = observation()
    end = observation(depth=5, xl=6, turn=8000)
    path = recorded(tmp_path, [action(start, end)], end)
    row = benchmark.run_report(trial(), path, MANIFEST)
    assert row["milestones"]["D5"] == {"turn": 8000, "location": "D:5"}
    assert row["milestones"]["D10"] is None
    assert row["milestone_status"]["D10"] == "not_observed_before_censoring"
    assert row["censored"] and not row["win"] and not row["death"]
    report = benchmark.aggregate([row])
    assert report["censored_turns"] == {"n": 1, "median": 8000, "max": 8000}
    assert report["milestones"]["D5"]["count"] == 1


def test_non_dungeon_depth_does_not_become_dungeon_milestone_or_lair_clear(tmp_path):
    start = observation(depth=4)
    lair = observation(place="Lair", depth=5, turn=10)
    zot = observation(place="Zot", depth=12, turn=20)
    path = recorded(tmp_path, [action(start, lair), action(lair, zot, sequence=2)], zot)
    result = benchmark.run_report(trial(), path, MANIFEST)
    assert result["max_dungeon_depth"] == 4
    assert result["milestones"]["D5"] is None
    assert result["milestones"]["D10"] is None
    assert result["milestones"]["Lair"]["location"] == "Lair:5"
    assert result["milestones"]["Lair_clear"] is None
    assert result["milestone_status"]["Lair_clear"] == "unknown_requires_review"


@pytest.mark.parametrize("outcome", ["error", "paused", "timeout", "stopped", "action_limit"])
def test_error_paused_and_bounded_outcomes_are_neither_deaths_nor_wins(tmp_path, outcome):
    directory = recorded(tmp_path, result=outcome)
    row = benchmark.run_report(trial(outcome=outcome), directory, MANIFEST)
    assert row["outcome"] == outcome and row["censored"]
    assert not row["death"] and not row["win"]


def test_death_exposes_chronological_windows_and_unproven_flags(tmp_path):
    monster = {"name": "orc warrior", "x": 1, "y": 0, "attitude": 0, "threat": 3}
    item = {"letter": "a", "name": "potion of heal wounds", "quantity": 1}
    before = observation(hp=6, monsters=[monster], inventory=[item])
    after = observation(hp=0, monsters=[monster], inventory=[item], dead=True, game_over=True,
                        messages=["The orc warrior hits you!", "You die..."], turn=1)
    directory = recorded(tmp_path, [action(before, after, kind="wait")], after,
                         result="dead", dead=True, game_over=True,
                         death_cause="The orc warrior hits you!", death_cause_source="last_damage_message")
    row = benchmark.run_report(trial(outcome="dead", dead=True, turns=1), directory, MANIFEST)
    assert row["death"] and not row["censored"]
    review = row["death_review"]
    assert review["earliest_flag_candidate"]["sequence"] == 1
    assert "not proven" in review["earliest_flag_candidate"]["status"]
    assert review["chronological_window"][0]["action"]["kind"] == "wait"
    assert row["encounters"][0]["monsters"] == ["orc warrior"]
    assert row["unused_emergencies_at_end"][0]["name"] == item["name"]
    assert len(row["dangerous_hp_drops"]) == 1
    assert row["death_cause_source"] == "last_damage_message"


@pytest.mark.parametrize("end_reason,win", [("won", True), ("quit", False), ("You have escaped!", False),
                                          ("Escaped with the Orb!", True)])
def test_win_requires_real_game_over_and_explicit_victory_evidence(tmp_path, end_reason, win):
    final = observation(game_over=True)
    directory = recorded(tmp_path, final=final, result="game_over", game_over=True,
                         details={"end_reason": end_reason})
    row = benchmark.run_report(trial(outcome="game_over"), directory, MANIFEST)
    assert row["win"] is win
    assert not row["death"]


def test_victory_text_without_terminal_evidence_is_not_win(tmp_path):
    directory = recorded(tmp_path, final=observation(messages=["You won the game!"]))
    row = benchmark.run_report(trial(), directory, MANIFEST)
    assert not row["win"]


def test_human_review_annotation_required_for_lair_clear(tmp_path):
    directory = recorded(tmp_path)
    unreviewed = {"Lair_clear": {"reviewed": False, "evidence": "I think so"}}
    assert benchmark.run_report(trial(), directory, MANIFEST, unreviewed)["milestones"]["Lair_clear"] is None
    reviewed = {"Lair_clear": {"reviewed": True, "evidence": "Public trace sequences 120-700 reviewed", "turn": 6500},
                "death_review": {"reviewer": "human", "sequence": 120, "notes": "reviewed"}}
    row = benchmark.run_report(trial(), directory, MANIFEST, reviewed)
    assert row["milestones"]["Lair_clear"]["human_reviewed"]


def test_wilson_interval_handles_zero_all_and_empty():
    assert benchmark.wilson(0, 0)["ci95"] == [None, None]
    zero, full = benchmark.wilson(0, 30), benchmark.wilson(30, 30)
    assert zero["ci95"][0] == pytest.approx(0)
    assert zero["ci95"][1] == pytest.approx(.11351339)
    assert full["ci95"][0] == pytest.approx(1 - zero["ci95"][1])
    assert full["ci95"][1] == pytest.approx(1)
    with pytest.raises(ValueError):
        benchmark.wilson(2, 1)


def test_gzip_action_and_event_inputs_and_lossless_compression(tmp_path):
    start, end = observation(), observation(turn=1, depth=5)
    directory = recorded(tmp_path, [action(start, end)], end)
    (directory / "evaluation.json").write_text("{}")
    (directory / "events.jsonl").write_text(json.dumps({"kind": "transport_error", "data": {"error": "timeout"}}) + "\n")
    original = (directory / "actions.jsonl").read_bytes()
    benchmark.compress_completed(tmp_path)
    assert not (directory / "actions.jsonl").exists()
    assert gzip.decompress((directory / "actions.jsonl.gz").read_bytes()) == original
    result = benchmark.run_report(trial(), directory, MANIFEST)
    assert result["milestones"]["D5"] is not None
    assert result["protocol_events"]["transport_error"] == 1


def test_compression_never_touches_incomplete_runs(tmp_path):
    directory = recorded(tmp_path)
    benchmark.compress_completed(tmp_path)
    assert (directory / "actions.jsonl").exists()
    assert not (directory / "actions.jsonl.gz").exists()


def test_collect_ingests_existing_one_seed_batches_and_gzip_results(tmp_path):
    batch = tmp_path / "seed-101" / "batch"
    run_root = batch / "runs"
    run_root.mkdir(parents=True)
    directory = recorded(run_root)
    row = trial(run_dir="old-machine/path/run")
    with gzip.open(batch / "results.jsonl.gz", "wt") as stream:
        stream.write(json.dumps(row) + "\n")
    (batch / "manifest.json").write_text(json.dumps(MANIFEST))
    result = benchmark.collect([tmp_path], "baseline", {})
    assert len(result) == 1 and result[0]["seed"] == 101
    assert result[0]["bounds"] == BOUNDS
    assert result[0]["trace_available"]


def test_paired_comparisons_exclude_hash_limits_and_missing_seed_mismatches(tmp_path):
    directory = recorded(tmp_path)
    baseline = benchmark.run_report(trial(), directory, MANIFEST)
    improved = deepcopy(baseline)
    improved["milestones"]["D5"] = {"turn": 500}
    result = benchmark.paired([baseline], [improved])
    assert result["matched_n"] == 1
    assert result["metrics"]["D5"]["improved_only"] == 1
    assert result["metrics"]["D5"]["paired_observed_fraction_delta"] == 1
    improved["pairing"]["initial_state_sha256"] = "different"
    improved["initial_common_schema_sha256"] = "different-public-start"
    improved["bounds"]["max_turns"] = 9000
    missing = deepcopy(baseline)
    missing["seed"] = 102
    result = benchmark.paired([baseline, missing], [improved])
    assert result["matched_n"] == 0
    assert result["mismatches"][0]["reasons"] == ["initial_common_schema_sha256", "run_bounds"]
    assert result["mismatches"][1]["reasons"] == ["missing_improved"]
    assert result["metrics"]["D5"]["exact_mcnemar_two_sided_p"] is None
    with pytest.raises(ValueError, match="Duplicate"):
        benchmark.paired([baseline, baseline], [improved])


def test_retreat_escape_evidence_is_visibility_not_safety_and_consumption_observed(tmp_path):
    enemy = {"name": "orc", "x": 1, "y": 0, "attitude": 0}
    before = observation(monsters=[enemy])
    after = observation(turn=1)
    directory = recorded(tmp_path, [action(before, after, objective="retreat", consumed={"scroll of blinking": 1})], after)
    row = benchmark.run_report(trial(), directory, MANIFEST)
    assert row["retreat_episodes"][0]["escape_evidence"].startswith("observed_no_visible_hostiles")
    assert row["consumables_used"] == {"scroll of blinking": 1}


def test_source_snapshot_is_independent_of_later_edits(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_text('VERSION = "baseline"\n')
    destination = tmp_path / "snapshot" / "dcss_bot"
    hashes = benchmark.source_snapshot(source, destination)
    (source / "__init__.py").write_text('VERSION = "improved"\n')
    assert (destination / "__init__.py").read_text() == 'VERSION = "baseline"\n'
    assert "__init__.py" in hashes


def test_launch_defaults_explicit_seed_sets_and_existing_directory_guard(tmp_path):
    args = benchmark.parser().parse_args(["launch", "--label", "baseline", "--output", str(tmp_path)])
    assert args.max_turns == 8000 and args.max_actions == 20000 and args.seconds_per_run == 1800
    assert args.record_seeds is None and args.split == "heldout"
    assert len(benchmark.HELDOUT_SEEDS) == 30 and benchmark.HELDOUT_SEEDS[0] == 101
    assert set(benchmark.DEVELOPMENT_SEEDS).isdisjoint(benchmark.HELDOUT_SEEDS)
    with pytest.raises(FileExistsError):
        asyncio.run(benchmark.launch(args))


@pytest.mark.asyncio
async def test_launch_bounds_concurrency_and_isolates_worker_packages_sockets(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_text("")
    output = tmp_path / "launch"
    active = 0
    peak = 0
    calls = []

    class Process:
        async def wait(self):
            nonlocal active
            await asyncio.sleep(.01)
            active -= 1
            return 0

        def send_signal(self, signal):
            pass

    async def subprocess(*command, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls.append((command, kwargs))
        return Process()

    monkeypatch.setattr(benchmark.asyncio, "create_subprocess_exec", subprocess)
    args = benchmark.parser().parse_args(["launch", "--label", "test", "--source", str(source),
                                         "--output", str(output), "--seeds", "101", "102", "103",
                                         "--concurrency", "2", "--record-seeds", "102"])
    assert await benchmark.launch(args) == 0
    assert peak == 2
    results = json.loads((output / "launcher-results.json").read_text())
    assert results["interrupted"] is False
    plan = json.loads((output / "plan.json").read_text())
    alias = Path(plan["socket_alias"])
    assert alias.resolve() == output / "private"
    alias.unlink()
    alias.parent.rmdir()


def test_common_initial_fingerprint_ignores_schema_additions_and_transport_identity():
    baseline = observation(name="AccountOne", messages=["Welcome"], input_mode=1)
    baseline["monsters"] = [{"id": 7, "name": "rat", "x": 2, "y": 0, "threat": 1, "attitude": 0}]
    improved = deepcopy(baseline)
    improved.update(name="AccountTwo", input_mode=0, piety=15, gold=0)
    improved["monsters"][0].update(id=99, speed=None)
    assert benchmark.initial_fingerprint(baseline) == benchmark.initial_fingerprint(improved)
    improved["hp"] -= 1
    assert benchmark.initial_fingerprint(baseline) != benchmark.initial_fingerprint(improved)
    del improved["turn"]
    assert benchmark.initial_fingerprint(improved) is None


def test_raw_hash_schema_change_is_diagnostic_not_a_waived_public_start_mismatch(tmp_path):
    directory = recorded(tmp_path)
    baseline = benchmark.run_report(trial(), directory, MANIFEST)
    improved = deepcopy(baseline)
    improved["pairing"]["initial_state_sha256"] = "new-observation-schema"
    improved["initial_additional_public_fields"] = {"piety": None}
    report = benchmark.paired([baseline], [improved])
    assert report["matched_n"] == 1 and not report["mismatches"]
    assert report["raw_initial_hash_differences"][0]["common_schema_equal"] is True
    improved["initial_common_schema_sha256"] = None
    report = benchmark.paired([baseline], [improved])
    assert report["matched_n"] == 0
    assert report["mismatches"][0]["reasons"] == ["initial_common_schema_sha256"]


def test_native_character_ready_is_initial_evidence_not_first_later_decision(tmp_path):
    initial = observation()
    before = observation(turn=12, depth=2)
    directory = recorded(tmp_path, [action(before, observation(turn=13, depth=2))])
    (directory / "events.jsonl").write_text(json.dumps({
        "kind": "character_ready", "data": {"observation": initial}}) + "\n")
    report = benchmark.run_report(trial(), directory, MANIFEST)
    assert report["initial_common_schema_sha256"] == benchmark.initial_fingerprint(initial)
    assert report["initial_common_schema_sha256"] != benchmark.initial_fingerprint(before)


def test_stall_and_invalid_action_categories_use_recorded_reason_evidence(tmp_path):
    directory = recorded(tmp_path)
    events = [
        {"kind": "paused", "data": {"reason": "No meaningful progress; stall"}},
        {"kind": "action_error", "data": {"type": "ValueError", "error": "Invalid action: bad"}},
        {"kind": "supervisor_error", "data": {"type": "ProtocolError", "error": "Bad UI"}},
    ]
    (directory / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    report = benchmark.run_report(trial(), directory, MANIFEST)
    assert report["failure_categories"]["stalls"] == 1
    assert report["failure_categories"]["invalid_actions"] == 1
    assert report["failure_categories"]["protocol_errors"] == 1


def test_native_dungeon_place_counts_milestones_but_branch_depth_does_not(tmp_path):
    start = observation(place="Dungeon",depth=4)
    fifth = observation(place="Dungeon",depth=5,turn=10)
    lair = observation(place="Lair",depth=5,turn=20)
    directory = recorded(tmp_path,[action(start,fifth),action(fifth,lair,sequence=2)],lair)
    row = benchmark.run_report(trial(),directory,MANIFEST)
    assert row["max_dungeon_depth"] == 5
    assert row["milestones"]["D5"]["turn"] == 10
    assert row["milestones"]["D10"] is None
    assert row["milestones"]["Lair"]["turn"] == 20


def test_strategy_uncertainty_and_monster_prose_are_not_execution_errors(tmp_path):
    before = observation(messages=["A terrible error would be to fight this wizard."])
    directory = recorded(tmp_path,[action(before,before)],before)
    events = [
        {"kind":"decision","data":{"action":{"reason":"uncertain damage estimate"},
                                  "observation":before}},
        {"kind":"action_error","data":{"error":"ProtocolError: wrong native interface","type":"ProtocolError"}},
    ]
    (directory/"events.jsonl").write_text("".join(json.dumps(event)+"\n" for event in events))
    row = benchmark.run_report(trial(),directory,MANIFEST)
    assert row["failure_categories"] == {"protocol_errors":1,"errors":1}


def test_postmortem_identification_is_not_reported_as_known_unused_healing(tmp_path):
    dead = observation(hp=0,dead=True,game_over=True,inventory=[
        {"letter":"a","name":"potion of heal wounds {unknown}","quantity":1},
        {"letter":"b","name":"potion of curing","quantity":2}])
    directory = recorded(tmp_path,final=dead,dead=True,game_over=True,result="dead")
    row = benchmark.run_report(trial(outcome="dead",dead=True),directory,MANIFEST)
    assert [item["letter"] for item in row["unused_emergencies_at_end"]] == ["b"]
    assert [item["letter"] for item in row["unidentified_emergencies_revealed_at_death"]] == ["a"]
    metrics = benchmark.aggregate([row])
    assert metrics["deaths_with_identified_emergencies_unused"] == 1
    assert metrics["unidentified_emergency_stacks_revealed_at_death"] == 1


@pytest.mark.parametrize("initial,final,loss", [
    ({"hp":45,"hp_max":45,"statuses":["Berserk"]},{"hp":30,"hp_max":30,"statuses":["Slow","-Berserk"]},0),
    ({"hp":45,"hp_max":45,"statuses":["Berserk"]},{"hp":22,"hp_max":30,"statuses":["Slow","-Berserk"]},8),
    ({"hp":30,"hp_max":30,"statuses":[]},{"hp":35,"hp_max":45,"statuses":["Berserk"]},10),
    ({"hp":30,"hp_max":30,"statuses":[]},{"hp":22,"hp_max":28,"statuses":["Drain"]},8),
])
def test_dangerous_loss_excludes_berserk_rescaling_but_keeps_real_harm(tmp_path,initial,final,loss):
    before,after = observation(**initial),observation(turn=1,**final)
    directory = recorded(tmp_path,[action(before,after)],after)
    report = benchmark.run_report(trial(),directory,MANIFEST)
    if loss:
        assert report["dangerous_hp_drops"][0]["observed_loss"] == loss
    else:
        assert report["dangerous_hp_drops"] == []


@pytest.mark.parametrize("gains,losses,p_value", [(6,0,1/32),(4,2,44/64),(0,0,1)])
def test_paired_attainment_uses_two_sided_exact_discordance_not_independent_rates(
        tmp_path,gains,losses,p_value):
    prototype = benchmark.run_report(trial(),recorded(tmp_path),MANIFEST)
    left,right = [],[]
    for index in range(gains+losses+3):
        a,b = deepcopy(prototype),deepcopy(prototype)
        a["seed"] = b["seed"] = 201+index
        if index<gains:
            b["milestones"]["D5"] = {"turn":500}
        elif index<gains+losses:
            a["milestones"]["D5"] = {"turn":500}
        left.append(a)
        right.append(b)
    result = benchmark.paired(left,right)
    assert result["metrics"]["D5"]["exact_mcnemar_two_sided_p"] == pytest.approx(p_value)
    reverse = benchmark.paired(right,left)
    assert reverse["metrics"]["D5"]["exact_mcnemar_two_sided_p"] == pytest.approx(p_value)
