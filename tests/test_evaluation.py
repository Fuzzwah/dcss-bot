import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

import pytest

from dcss_bot import evaluation
from dcss_bot.characters import get_character
from dcss_bot.config import Config
from dcss_bot.evaluation import Evaluation, aggregate, expected_version, observable_state, run_evidence
from dcss_bot.models import Action, Cell, Monster, Observation
from dcss_bot.recording import Recorder


CHARACTER = get_character("mibe")


def trial(agent="rule", seed=1, repeat=1, **changes):
    data = dict(agent=agent, seed=seed, repeat=repeat, valid_start=True,
                outcome="turn_limit", turns=100, dead=False, deepest_depth=1,
                max_xl=1, kills=2, reason="", initial_state_sha256="initial",
                trajectory_sha256="trace", rc_sha256="rc",
                character=CHARACTER.metadata(), character_module="dcss_bot.characters.mibe:PROFILE")
    data.update(changes)
    return data


def test_capped_death_and_failed_start_are_not_merged_into_survival_success():
    report = aggregate([
        trial(), trial(repeat=2, outcome="dead", dead=True, turns=40),
        trial(repeat=3, outcome="paused", turns=20, reason="No escape"),
        trial(repeat=4, outcome="error", valid_start=False, turns=0, reason="Seed not confirmed"),
    ])["agents"]["rule"]
    assert report["runs"] == 4 and report["valid_starts"] == 3
    assert report["deaths"] == 1 and report["turn_limit_censored"] == 1
    assert report["mean_observed_turns"] == pytest.approx(160 / 3)
    assert report["median_observed_turns"] == 40
    assert report["failure_reasons"] == {"No escape": 1, "Seed not confirmed": 1}


def test_repeated_seeds_report_different_trace_even_with_identical_results():
    result = aggregate([trial(), trial(repeat=2, trajectory_sha256="changed combat")])["repeatability"][0]
    assert result["same_initial_state"] and result["same_rc"]
    assert result["same_observable_trace"] is False


@pytest.mark.parametrize("change", [
    {"initial_state_sha256": "different start"}, {"rc_sha256": "different rc"}, {"valid_start": False},
])
def test_paired_results_reject_unmatched_starting_conditions(change):
    result = aggregate([trial(), trial(agent="other", **change)])
    assert result["paired_comparisons"][0]["comparable_start"] is False


def test_fingerprint_ignores_transport_identity_but_not_visible_danger():
    first = Observation(name="AccountOne", hp=20, hp_max=20,
                        monsters=(Monster(10, "centaur", 2, 0, 2),),
                        cells=(Cell(0, 0, 33, "@", True),)).to_dict()
    second = Observation(name="AccountTwo", hp=20, hp_max=20,
                         monsters=(Monster(99, "centaur", 2, 0, 2),),
                         cells=(Cell(0, 0, 33, "@", True),),
                         messages=("Welcome AccountTwo",)).to_dict()
    assert observable_state(first) == observable_state(second)
    second["hp"] = 10
    assert observable_state(first) != observable_state(second)


def test_recorded_evidence_counts_turn_advancement_and_actual_equipment_change(tmp_path):
    recorder = Recorder(tmp_path, {})
    obs = Observation(species="Minotaur", god="Trog", hp=20, hp_max=20,
                      messages=("Welcome, Example the Minotaur Berserker.",
                                "Game seed: 18446744073709551615 (custom seed); "))
    recorder.event("character_ready", {"observation": obs.to_dict()})
    recorder.record(obs, Action("pickup", objective="equipment"), obs, .1)
    recorder.record(obs, Action("move", "l", objective="explore"), replace(obs, turn=1), .1)
    recorder.finish("turn_limit", replace(obs, turn=1))
    recorder.close()
    evidence = run_evidence(recorder.path, CHARACTER)
    assert evidence["initial_turn"] == 0 and evidence["character_verified"]
    assert evidence["engine_seed"] == 2**64 - 1
    assert evidence["meaningful_actions"] == 1
    assert evidence["equipment_actions"][0]["inventory_changed"] is False
    assert evidence["objectives"] == {"equipment": 1, "explore": 1}


@pytest.mark.parametrize("plan", [Evaluation((0,)), Evaluation((-1,)), Evaluation((2**64,)),
                                    Evaluation((1, 1)), Evaluation((1,), repeats=0),
                                    Evaluation((1,), seconds_per_run=float("nan"))])
def test_evaluation_rejects_random_ambiguous_and_unbounded_trials(plan):
    with pytest.raises(ValueError):
        plan.validate()


def test_evaluation_requires_exact_reported_release_not_a_version_prefix():
    assert expected_version("Dungeon Crawl Stone Soup 0.34.1 (tiles)")
    assert not expected_version("Dungeon Crawl Stone Soup 0.34.10 (tiles)")
    assert not expected_version("Dungeon Crawl Stone Soup 0.35-a0")
    assert not expected_version(None)


def recorded_exploration(tmp_path, initial, steps, *, character_ready=True):
    recorder = Recorder(tmp_path, {})
    if character_ready:
        recorder.event("character_ready", {"observation": initial.to_dict()})
    before = initial
    for action, after in steps:
        recorder.record(before, action, after, .1)
        before = after
    recorder.finish("turn_limit", before)
    recorder.close()
    return recorder.path


def test_exploration_preserves_floor_coordinates_and_counts_only_actual_movement(tmp_path):
    cells = (Cell(0, 0, 33), Cell(1, 0, 33))
    start = Observation(place="D", depth=1, hp=20, hp_max=20, cells=cells)
    revealed = cells + (Cell(2, 0, 33), Cell(3, 0, 4))
    first = replace(start, turn=1, position=(1, 0), cells=revealed)
    back = replace(first, turn=3, position=(0, 0))
    attack = replace(back, turn=4)
    rest = replace(attack, turn=7)
    stairs = replace(rest, turn=8, position=(1, 0))
    second_floor = replace(start, depth=2, turn=9)
    second_move = replace(second_floor, turn=12, position=(1, 0), cells=revealed)
    returned = replace(stairs, turn=13)
    returned_move = replace(returned, turn=14, position=(0, 0))
    path = recorded_exploration(tmp_path, start, [
        (Action("move", "l", objective="explore"), first),
        (Action("move", "h", objective="explore"), back),
        (Action("move", "l", objective="combat"), attack),
        (Action("wait", objective="recover"), rest),
        (Action("move", "l", objective="explore"), stairs),
        (Action("stairs_down", objective="descend"), second_floor),
        (Action("move", "l", objective="explore"), second_move),
        (Action("stairs_up", objective="ascend"), returned),
        (Action("move", "h", objective="explore"), returned_move),
        (Action("stairs_down", objective="descend"), replace(returned_move, turn=15)),
    ])
    metrics = run_evidence(path, CHARACTER)["exploration"]
    first_floor, second_floor = metrics["locations"]["D:1"], metrics["locations"]["D:2"]
    assert first_floor["first_arrival_turn"] == 0
    assert first_floor["first_descent_turn"] == 9
    assert second_floor["first_arrival_turn"] == 9
    assert second_floor["first_descent_turn"] is None
    assert first_floor["known_walkable_cells"] == second_floor["known_walkable_cells"] == 3
    assert first_floor["distinct_positions_visited"] == second_floor["distinct_positions_visited"] == 2
    assert first_floor["revisit_movement_actions"] == 3
    assert second_floor["revisit_movement_actions"] == 0
    assert first_floor["turns"] == 11
    assert second_floor["turns"] == 4
    assert metrics["totals"] == {
        "actions": 10, "turns": 15, "movement_actions": 5, "revisit_movement_actions": 3,
        "revealed_walkable_cells": 4, "reveal_observed_actions": 10,
        "exploration_actions": 5, "exploration_turns": 8, "exploration_revealed_walkable_cells": 2,
        "exploration_reveal_observed_actions": 5, "exploration_reveal_observed_turns": 8,
        "no_reveal_exploration_actions": 3, "no_reveal_exploration_turns": 4,
        "known_walkable_cells": 6, "distinct_positions_visited": 4,
    }
    assert metrics["objective_costs"] == {
        "explore": {"actions": 5, "turns": 8}, "combat": {"actions": 1, "turns": 1},
        "recover": {"actions": 1, "turns": 3}, "descend": {"actions": 2, "turns": 2},
        "ascend": {"actions": 1, "turns": 1},
    }
    assert second_floor["objective_costs"]["explore"] == {"actions": 1, "turns": 3}
    assert metrics["last_observed_turn"] == 15
    assert not any(metrics["missing_data"].values())
    summary = aggregate([trial(exploration=metrics)])["agents"]["rule"]["exploration"]
    assert summary["revisit_fraction_of_movement_actions"] == pytest.approx(3 / 5)
    assert summary["exploration_turns_per_revealed_walkable_cell"] == 4
    assert summary["locations"]["D:2"]["objective_costs"]["explore"] == {"actions": 1, "turns": 3}


def test_exploration_does_not_infer_intermediate_multiturn_positions_or_merge_branches(tmp_path):
    start = Observation(place="D", depth=1, turn=20, hp=20, hp_max=20,
                        cells=(Cell(0, 0, 33),))
    far = replace(start, turn=30, position=(10, 0),
                  cells=(Cell(0, 0, 33), Cell(10, 0, 33)))
    branch = replace(start, place="Lair", turn=31)
    path = recorded_exploration(tmp_path, start, [
        (Action("explore"), far), (Action("stairs_down", objective="descend"), branch),
    ], character_ready=False)
    metrics = run_evidence(path, CHARACTER)["exploration"]
    assert metrics["locations"]["D:1"]["first_arrival_turn"] == 20
    assert metrics["locations"]["Lair:1"]["first_arrival_turn"] == 31
    assert metrics["totals"]["distinct_positions_visited"] == 3
    assert metrics["totals"]["known_walkable_cells"] == 3
    assert metrics["totals"]["exploration_turns"] == 10
    assert metrics["totals"]["movement_actions"] == 1
    assert metrics["totals"]["revisit_movement_actions"] == 0


def test_missing_maps_are_not_counted_as_no_reveal_exploration(tmp_path):
    start = Observation(place="D", depth=1, hp=20, hp_max=20,
                        cells=(Cell(0, 0, 33),))
    path = recorded_exploration(tmp_path, start, [
        (Action("move", "l", objective="explore"), replace(start, turn=5, position=(1, 0))),
    ], character_ready=False)
    # Model an older/incomplete public recorder row, without inventing terrain.
    action_path = path / "actions.jsonl"
    row = json.loads(action_path.read_text())
    del row["after"]["cells"]
    del row["after"]["position"]
    action_path.write_text(json.dumps(row) + "\n")
    metrics = run_evidence(path, CHARACTER)["exploration"]
    assert metrics["totals"]["exploration_turns"] == 5
    assert metrics["totals"]["movement_actions"] == 0
    assert metrics["totals"]["no_reveal_exploration_actions"] == 0
    assert metrics["totals"]["exploration_reveal_observed_actions"] == 0
    assert metrics["totals"]["known_walkable_cells"] == 1
    assert metrics["missing_data"]["map_observations"] == 1
    assert metrics["missing_data"]["position_observations"] == 1


def test_exploration_aggregation_keeps_absent_arrivals_and_missing_reports_separate(tmp_path):
    start = Observation(place="D", depth=1, hp=20, hp_max=20,
                        cells=(Cell(0, 0, 33),))
    shallow = run_evidence(recorded_exploration(tmp_path, start, []), CHARACTER)["exploration"]
    deep = run_evidence(recorded_exploration(tmp_path, start, [
        (Action("wait", objective="recover"), replace(start, turn=4)),
        (Action("stairs_down", objective="descend"), replace(start, depth=2, turn=5)),
    ]), CHARACTER)["exploration"]
    report = aggregate([
        trial(agent="baseline", exploration=shallow, outcome="dead", dead=True),
        trial(agent="baseline", repeat=2, exploration=shallow),
        trial(agent="baseline", repeat=3),
        trial(agent="baseline", repeat=4, valid_start=False, exploration=deep),
        trial(agent="frontier", exploration=deep, outcome="paused"),
    ])
    baseline = report["agents"]["baseline"]["exploration"]
    assert baseline["eligible_runs"] == 3
    assert baseline["runs_with_metrics"] == 2
    assert baseline["runs_missing_metrics"] == 1
    absent = baseline["locations"]["D:2"]
    assert absent["eligible_runs"] == 2
    assert absent["observed_runs"] == 0 and absent["not_observed_runs"] == 2
    assert absent["not_observed_by_outcome"] == {"dead": 1, "turn_limit": 1}
    assert absent["first_arrival_turn"] == {"count": 0, "mean": None, "median": None}
    assert absent["first_descent_turn"] == {"count": 0, "mean": None, "median": None}
    assert baseline["revisit_fraction_of_movement_actions"] is None
    assert baseline["exploration_turns_per_revealed_walkable_cell"] is None
    frontier = report["agents"]["frontier"]["exploration"]
    assert frontier["locations"]["D:2"]["first_arrival_turn"] == {"count": 1, "mean": 5, "median": 5}
    assert frontier["locations"]["D:1"]["first_descent_turn"] == {"count": 1, "mean": 5, "median": 5}
    assert frontier["objective_costs"]["recover"] == {"actions": 1, "turns": 4}
    assert report["paired_comparisons"][0]["agents"]["frontier"]["exploration"] == deep


def test_empty_recording_does_not_invent_arrivals_or_map_coverage(tmp_path):
    recorder = Recorder(tmp_path, {})
    recorder.close()
    metrics = run_evidence(recorder.path, CHARACTER)["exploration"]
    assert metrics["locations"] == {}
    assert metrics["last_observed_turn"] is None
    assert metrics["totals"]["known_walkable_cells"] == 0
    legacy = aggregate([trial()])["agents"]["rule"]["exploration"]
    assert legacy["runs_with_metrics"] == 0
    assert legacy["runs_missing_metrics"] == 1
    assert legacy["locations"] == {}


@pytest.mark.parametrize("changes", [
    {"species": "Human"},
    {"god": "Okawaru"},
    {"messages": ("Welcome, Example the Minotaur Fighter.",)},
    {"messages": ("Welcome back, Example the Minotaur Berserker.",)},
    {"messages": ()},
])
def test_start_evidence_requires_matching_species_god_and_native_background(tmp_path, changes):
    initial = Observation(species="Minotaur", god="Trog",
                          messages=("Welcome, Example the Minotaur Berserker.",))
    path = recorded_exploration(tmp_path, replace(initial, **changes), [])
    evidence = run_evidence(path, CHARACTER)
    assert not evidence["character_verified"]
    assert evidence["character"] == CHARACTER.metadata()


def test_start_evidence_uses_selected_profile_instead_of_default_combo(tmp_path):
    selected = replace(CHARACTER, identifier="fixture", species="Human", background="Fighter", god="")
    initial = Observation(species="Human", god="",
                          messages=("Welcome, Example the Human Fighter.",))
    path = recorded_exploration(tmp_path, initial, [])
    assert run_evidence(path, selected)["character_verified"]
    assert not run_evidence(path, CHARACTER)["character_verified"]


def test_same_profile_rows_remain_comparable_despite_selector_case():
    rows = [trial(character_requested="MiBe"), trial(agent="other", character_requested="mibe"),
            trial(repeat=2, character_requested="mibe")]
    report = aggregate(rows)
    assert report["character"] == CHARACTER.metadata()
    assert report["character_module"] == "dcss_bot.characters.mibe:PROFILE"
    assert report["paired_comparisons"][0]["comparable_start"]
    assert report["repeatability"][0]["same_observable_trace"]


@pytest.mark.parametrize("changes", [
    {"character": {**CHARACTER.metadata(), "species": "Human"}},
    {"character": {**CHARACTER.metadata(), "background": "Fighter"}},
    {"character": {**CHARACTER.metadata(), "god": "Okawaru"}},
    {"character_module": "installed_character:PROFILE"},
])
@pytest.mark.parametrize("other", [{"agent": "other"}, {"repeat": 2}])
def test_comparisons_reject_different_characters_even_with_matching_hashes(changes, other):
    with pytest.raises(ValueError, match="different or unknown character"):
        aggregate([trial(), trial(**other, **changes)])


@pytest.mark.parametrize("metadata", [None, {}, {"species": "Minotaur", "god": "Trog"}])
def test_historical_rows_keep_metrics_but_do_not_inherit_default_character(metadata):
    rows = [trial(), trial(agent="other"), trial(repeat=2)]
    for row in rows:
        del row["character"]
        del row["character_module"]
        if metadata is not None:
            row["character"] = metadata
    report = aggregate(rows)
    assert report["character"] is None and report["character_module"] is None
    assert report["agents"]["rule"]["runs"] == 2
    assert not report["paired_comparisons"][0]["comparable_start"]
    assert not report["repeatability"][0]["same_initial_state"]
    assert not report["repeatability"][0]["same_observable_trace"]
    with pytest.raises(ValueError, match="different or unknown character"):
        aggregate([trial(), rows[1]])


def test_unknown_character_fails_before_creating_evaluation_directories(tmp_path):
    config = Config(character="not-installed", run_root=tmp_path / "runs",
                    control_socket=tmp_path / "state" / "control.sock")
    with pytest.raises(ValueError, match="Unknown character"):
        asyncio.run(evaluation.run_evaluation(config, Evaluation((1,))))
    assert not config.run_root.exists()
    assert not config.control_socket.parent.exists()


@pytest.mark.parametrize("background", ["Berserker", "Fighter"])
def test_evaluation_preserves_installed_profile_provenance_and_rejects_wrong_start(
        tmp_path, monkeypatch, background):
    module_path = tmp_path / "evaluation_character.py"
    module_path.write_text(
        "from dataclasses import replace\n"
        "from dcss_bot.characters.mibe import PROFILE as MIBE\n"
        "PROFILE = replace(MIBE, identifier='installed-mibe')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(sys.modules, "evaluation_character", None)
    monkeypatch.delitem(sys.modules, "evaluation_character")
    selector = "evaluation_character:PROFILE"
    config = Config(character=selector, run_root=tmp_path / "runs",
                    control_socket=tmp_path / "state" / "control.sock")

    class RecordedSupervisor:
        def __init__(self, config, agent, adapter, recorder):
            self.recorder = recorder
            self.observation = Observation(
                species="Minotaur", god="Trog", hp=20, hp_max=20,
                messages=(f"Welcome, Example the Minotaur {background}.",
                          f"Game seed: {config.seed} (custom seed)"))
            adapter.seed_confirmed = True
            adapter.seed_rc = "fixture rc"
            adapter.seed_rc_sha256 = hashlib.sha256(adapter.seed_rc.encode()).hexdigest()
            adapter.engine_version = "Dungeon Crawl Stone Soup 0.34.1 (tiles)"

        async def run(self, *, stop_on_pause):
            self.recorder.event("character_ready", {"observation": self.observation.to_dict()})
            result = self.recorder.finish("turn_limit", self.observation)
            self.recorder.close()
            return result

    monkeypatch.setattr(evaluation, "Supervisor", RecordedSupervisor)
    report = asyncio.run(evaluation.run_evaluation(config, Evaluation((1,))))
    directory = Path(report["directory"])
    manifest = json.loads((directory / "manifest.json").read_text())
    row = json.loads((directory / "results.jsonl").read_text())
    metadata = json.loads((Path(row["run_dir"]) / "metadata.json").read_text())
    expected = get_character(selector).metadata()
    source_hash = hashlib.sha256(module_path.read_bytes()).hexdigest()
    for document in (report, manifest, row, metadata):
        assert document["character_requested"] == selector
        assert document["character"] == expected
        assert document["character_module"] == selector
        assert document["character_source_sha256"] == source_hash
    package = Path(evaluation.__file__).parent
    assert manifest["package_sources_sha256"]["characters/mibe.py"] == hashlib.sha256(
        (package / "characters" / "mibe.py").read_bytes()).hexdigest()
    assert "characters/base.py" in manifest["package_sources_sha256"]
    assert row["character_verified"] == row["valid_start"] == (background == "Berserker")
    assert report["error"] == (background != "Berserker")
    if background != "Berserker":
        assert row["outcome"] == "error"
        assert selector in row["reason"]
