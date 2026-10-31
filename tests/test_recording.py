import asyncio
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from dcss_bot.config import Config, DCSS_COMMIT, DCSS_VERSION
from dcss_bot.models import Action, Cell, Item, Monster, Observation
from dcss_bot.recording import Recorder, VideoRecorder


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def observation(**changes):
    return replace(Observation(name="MiBeBot", species="Minotaur", god="Trog", turn=400,
                               place="Dungeon", depth=4, hp=30, hp_max=30, xl=5,
                               input_mode=1), **changes)


def test_unique_metadata_and_flushed_complete_decisions(tmp_path):
    config = Config(password="not-for-the-record")
    recorder = Recorder(tmp_path, config.public_dict())
    other = Recorder(tmp_path, config.public_dict())
    try:
        assert recorder.path != other.path
        metadata = json.loads((recorder.path / "metadata.json").read_text())
        assert metadata["run_id"] == recorder.path.name
        assert datetime.fromisoformat(metadata["started_at"]).tzinfo is not None
        assert metadata["engine"] == {"name": "DCSS", "version": DCSS_VERSION, "commit": DCSS_COMMIT}
        assert metadata["config"]["character"]["god"] == "Trog"
        before = observation(inventory=(Item(1, "b", "a potion of curing"),),
                             monsters=(Monster(3, "rat", 1, 0),),
                             cells=(Cell(0, 0, 33, ".", True),), messages=("You hit the rat.",))
        after = replace(before, turn=401, monsters=(), messages=before.messages + ("You kill the rat!",))
        action = Action("move", "l", "Strike adjacent rat", "combat")
        recorder.record(before, action, after, 0.125)
        # Read before close: a crashed process must leave inspectable complete lines.
        actions = rows(recorder.path / "actions.jsonl")
        expected_before = json.loads(json.dumps(before.to_dict()))
        expected_after = json.loads(json.dumps(after.to_dict()))
        assert actions[0]["before"] == expected_before
        assert actions[0]["after"] == expected_after
        assert actions[0]["action"] == action.to_dict()
        assert actions[0]["elapsed"] == 0.125
        assert actions[0]["outcome"]["new_messages"] == ["You kill the rat!"]
        observations = rows(recorder.path / "observations.jsonl")
        assert [(row["sequence"], row["phase"]) for row in observations] == [(1, "before"), (1, "after")]
        recorder.event("dispatch_error", {"error": "Connection closed; command not replayed"})
        recorder.publish_status({"state": "paused", "turn": after.turn})
        assert json.loads((recorder.path / "status.json").read_text())["state"] == "paused"
        assert not list(recorder.path.glob("*.tmp"))
        recorder.finish("stopped", after)
        events = rows(recorder.path / "events.jsonl")
        assert [row["kind"] for row in events] == ["run_started", "decision", "dispatch_error", "run_finished"]
        assert [row["sequence"] for row in events] == [1, 2, 3, 4]
    finally:
        recorder.close()
        other.close()


def test_resumed_turn_delta_maxima_and_death_evidence(tmp_path):
    recorder = Recorder(tmp_path, {})
    before = observation(hp=8, messages=("You kill an old rat!",))
    after = replace(before, turn=402, depth=5, xl=6, hp=6, position=(-1, 0),
                    messages=before.messages + ("The ogre hits you!",))
    recorder.record(before, Action("move", "h", "Low HP; escape", "retreat"), after, 0.4)
    dead = replace(after, turn=403, depth=4, hp=0, dead=True, game_over=True,
                   messages=after.messages + ("You are slain by an ogre!", "You die..."))
    recorder.record(after, Action("wait", reason="No safe exit", objective="combat"), dead, 0.2)
    summary = recorder.finish("dead", dead, {"disconnect": "clean"})
    recorder.close()
    assert summary["start_turn"] == 400
    assert summary["turns_survived"] == 3
    assert summary["total_game_turn"] == 403
    assert summary["deepest_depth"] == 5
    assert summary["deepest_location"] == "Dungeon:5"
    assert summary["max_xl"] == 6
    assert summary["kills"] == 0  # The resumed message tail is not this run's kill.
    assert summary["retreats"] == 1
    assert summary["emergencies"] == 2
    assert summary["decisions"] == 2
    assert summary["decision_seconds"] == pytest.approx(0.6)
    assert summary["dead"] and summary["game_over"]
    assert summary["death_cause"] == "You are slain by an ogre!"
    assert summary["death_cause_source"] == "explicit_message"
    assert summary["recent_messages"][-1] == "You die..."
    assert summary["final_observation"]["hp"] == 0
    assert summary["details"] == {"disconnect": "clean"}
    assert json.loads((recorder.path / "summary.json").read_text()) == summary
    assert json.loads((recorder.path / "status.json").read_text())["state"] == "finished"


def test_consumables_count_observed_depletion_and_identification(tmp_path):
    recorder = Recorder(tmp_path, {})
    before = observation(inventory=(Item(0, "a", "3 smoky potions", 3),
                                    Item(1, "b", "a scroll of teleportation"),
                                    Item(2, "c", "a hand axe", equipped=True)))
    unchanged = replace(before, prompt="You cannot drink right now.")
    recorder.record(before, Action("quaff", "a", "Try healing"), unchanged, 0.1)
    after = replace(unchanged, turn=401, inventory=(Item(0, "a", "2 potions of curing", 2),
                                                  *before.inventory[1:]))
    recorder.record(unchanged, Action("quaff", "a", "Heal", "emergency recovery"), after, 0.1)
    selection = replace(after, prompt="Read which scroll?", input_mode=7)
    recorder.record(after, Action("read", "b", "Escape"), selection, 0.1)
    consumed = replace(selection, turn=402, inventory=(after.inventory[0], after.inventory[2]), input_mode=1)
    recorder.record(selection, Action("confirm", "y", "Confirm reading"), consumed, 0.1)
    last = replace(consumed, turn=403, inventory=(Item(2, "c", "a hand axe", equipped=True),))
    recorder.record(consumed, Action("quaff", "a", "Drink observed stack"), last, 0.1)
    summary = recorder.finish("stopped", last)
    recorder.close()
    assert summary["consumables_used"] == {"potion of curing": 3, "scroll of teleportation": 1}
    assert summary["consumables_total"] == 4
    actions = rows(recorder.path / "actions.jsonl")
    assert actions[0]["outcome"]["consumables_used"] == {}
    assert actions[2]["outcome"]["consumables_used"] == {}
    assert actions[3]["outcome"]["consumables_used"] == {"scroll of teleportation": 1}
    assert actions[-1]["outcome"]["consumables_used"] == {"potion of curing": 2}


def test_overlapping_tails_preserve_distinct_repeated_kills(tmp_path):
    recorder = Recorder(tmp_path, {})
    kill = "You kill the rat!"
    before = observation(messages=("Welcome back.", kill, "You rest."))
    first = replace(before, turn=401, messages=(kill, "You rest.", "You hit the rat.", kill))
    recorder.record(before, Action("move", "l", objective="combat"), first, 0.1)
    # Repeated observations and acknowledgements must not recount the same tail.
    recorder.record(first, Action("more", objective="interface"), first, 0.1)
    second = replace(first, turn=402, messages=("You hit the rat.", kill, "You rest.", kill))
    recorder.record(first, Action("move", "l", objective="combat"), second, 0.1)
    third = replace(second, turn=403, messages=second.messages + (kill,))
    recorder.record(second, Action("move", "l", objective="combat"), third, 0.1)
    summary = recorder.finish("stopped", third)
    recorder.close()
    assert summary["kills"] == 3
    assert [row["outcome"]["new_messages"] for row in rows(recorder.path / "actions.jsonl")] == [
        ["You hit the rat.", kill], [], ["You rest.", kill], [kill]]


def test_final_unrecorded_messages_count_once_and_finish_is_idempotent(tmp_path):
    recorder = Recorder(tmp_path, {})
    before = observation()
    after = replace(before, turn=401)
    recorder.record(before, Action("wait"), after, 0.1)
    terminal = replace(after, turn=402, dead=True, hp=0,
                       messages=("You destroy the skeleton!", "The ogre hits you!", "You die..."))
    summary = recorder.finish("dead", terminal)
    assert summary["kills"] == 1
    assert summary["death_cause"] == "The ogre hits you!"
    assert summary["death_cause_source"] == "last_damage_message"
    with pytest.raises(RuntimeError, match="finished"):
        recorder.record(after, Action("wait"), terminal, 0.1)
    recorder.close()
    assert recorder.finish("different", before) == summary
    recorder.close()
    assert sum(row["kind"] == "run_finished" for row in rows(recorder.path / "events.jsonl")) == 1


def test_finish_without_decisions_has_no_invented_survival_or_cause(tmp_path):
    recorder = Recorder(tmp_path, {})
    summary = recorder.finish("dead", observation(turn=900, dead=True, hp=0, messages=("You die...",)))
    recorder.close()
    assert summary["decisions"] == summary["turns_survived"] == summary["kills"] == 0
    assert summary["total_game_turn"] == 900
    assert summary["death_cause"] is None


def test_all_outputs_redact_credentials_and_authenticated_urls(tmp_path):
    secret = "do-not-leak-this-password"
    token = "private-token-value"
    recorder = Recorder(tmp_path, {"password": secret,
                                  "url": f"ws://bot:{secret}@localhost:8080/socket?token={token}",
                                  "nested": {"api_key": "private-api-value"}})
    recorder.event("error", {"password": secret, "error": f"request failed for {secret} and {token}"})
    recorder.publish_status({"state": "paused", "password": secret})
    before = observation(messages=(f"Diagnostic: {secret}",))
    recorder.record(before, Action("wait", reason=f"Error with {token}"), before, 0.1)
    recorder.finish("stopped", before, {"error": f"api_key=private-api-value; password={secret}"})
    recorder.close()
    for path in recorder.path.iterdir():
        text = path.read_text()
        assert secret not in text
        assert token not in text
        assert "private-api-value" not in text
        assert "bot:" not in text
    metadata = json.loads((recorder.path / "metadata.json").read_text())
    assert metadata["password"] == "[REDACTED]"
    assert metadata["nested"]["api_key"] == "[REDACTED]"
    assert metadata["url"].startswith("ws://localhost:8080/socket?")


@pytest.mark.parametrize("url", ["ws://localhost/#watch-MiBeBot", "http://bot:secret@localhost/#watch-MiBeBot",
                                  "http://localhost/#play-MiBeBot", "http://localhost/#watch-"])
async def test_video_rejects_nonanonymous_or_nonspectator_urls_without_browser(tmp_path, url):
    recorder = VideoRecorder(url, tmp_path)
    with pytest.raises(ValueError):
        await recorder.start()
    assert await recorder.close() is None
    assert await recorder.close() is None


class CaptureRig:
    """Small browser/filesystem model: videos become readable only at close."""

    def __init__(self):
        self.contexts = []
        self.closed = False
        self.stopped = False
        self.state = "initial"
        self.navigation_started = asyncio.Event()
        self.navigation_gate = None
        self.paint_error = None
        self.join_error = False
        self.join_started = asyncio.Event()
        self.join_gate = None
        self.process = None

    async def start(self):
        return SimpleNamespace(chromium=self, stop=self.stop)

    async def stop(self):
        self.stopped = True

    async def launch(self, **kwargs):
        return self

    async def new_context(self, *, record_video_dir, **kwargs):
        context = CaptureContext(self, Path(record_video_dir), len(self.contexts))
        self.contexts.append(context)
        return context

    async def close(self):
        for context in self.contexts:
            await context.close()
        self.closed = True

    async def subprocess(self, *args, cwd, **kwargs):
        self.process = CaptureJoin(self, Path(cwd), args)
        return self.process


class CaptureContext:
    def __init__(self, rig, directory, index):
        self.rig = rig
        self.source = directory / f"browser-{index}.webm"
        self.frames = []
        self.closed = False
        self.page = CapturePage(self)

    async def new_page(self):
        return self.page

    async def close(self):
        if not self.closed:
            self.source.write_text("\n".join(self.frames) + "\n")
            self.closed = True


class CapturePage:
    def __init__(self, context):
        self.context = context
        self.video = self

    async def goto(self, *args, **kwargs):
        rig = self.context.rig
        rig.navigation_started.set()
        if rig.navigation_gate is not None:
            await rig.navigation_gate.wait()
        self.context.frames.append(rig.state)

    def locator(self, selector):
        return self

    async def wait_for(self, **kwargs):
        pass

    async def wait_for_function(self, *args, **kwargs):
        pass

    async def screenshot(self, *, path):
        Path(path).write_bytes(b"native spectator screenshot")

    async def evaluate(self, expression):
        if self.context.rig.paint_error:
            raise self.context.rig.paint_error
        self.context.frames.append(self.context.rig.state)

    async def path(self):
        if not self.context.closed:
            raise RuntimeError("Video is not finalized")
        return self.context.source


class CaptureJoin:
    def __init__(self, rig, directory, args):
        self.rig = rig
        self.directory = directory
        self.args = args
        self.returncode = None
        self.killed = False

    async def communicate(self):
        self.rig.join_started.set()
        if self.rig.join_gate is not None:
            await self.rig.join_gate.wait()
        if self.rig.join_error:
            self.returncode = 1
            return b"", b"invalid segment"
        manifest = self.directory / self.args[self.args.index("-i") + 1]
        segments = [self.directory / line.removeprefix("file '").removesuffix("'")
                    for line in manifest.read_text().splitlines()]
        (self.directory / self.args[-1]).write_bytes(b"".join(path.read_bytes() for path in segments))
        self.returncode = 0
        return b"", b""

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.fixture
def capture_rig(monkeypatch):
    rig = CaptureRig()
    monkeypatch.setitem(sys.modules, "playwright.async_api",
                        SimpleNamespace(async_playwright=lambda: rig))
    monkeypatch.setattr("dcss_bot.recording.shutil.which", lambda name: "/tools/ffmpeg")
    monkeypatch.setattr("dcss_bot.recording.asyncio.create_subprocess_exec", rig.subprocess)
    return rig


async def test_initially_paused_close_never_opens_capture_or_checks_dependencies(tmp_path, monkeypatch):
    def unexpected_dependency_check(name):
        raise AssertionError("An idle recorder must not initialize video dependencies")

    monkeypatch.setattr("dcss_bot.recording.shutil.which", unexpected_dependency_check)
    path = tmp_path / "not-created"
    recorder = VideoRecorder("http://localhost/#watch-MiBeBot", path)
    await recorder.start(paused=True)
    await recorder.pause()
    assert await recorder.close() is None
    assert await recorder.close() is None
    assert not path.exists()


async def test_video_missing_ffmpeg_fails_only_when_capture_is_requested(tmp_path, monkeypatch):
    monkeypatch.setattr("dcss_bot.recording.shutil.which", lambda name: None)
    recorder = VideoRecorder("http://localhost/#watch-MiBeBot", tmp_path)
    await recorder.start(paused=True)
    with pytest.raises(RuntimeError, match="FFmpeg"):
        await recorder.resume()
    assert await recorder.close() is None
    assert not list(tmp_path.iterdir())


async def test_video_segments_omit_pauses_and_keep_final_single_step_frame(tmp_path, capture_rig):
    path = tmp_path / "run with spaces and 'quotes'"
    recorder = VideoRecorder("http://localhost/#watch-MiBeBot", path)
    await recorder.start(paused=True)
    for index in range(3):
        capture_rig.state = f"before-{index}"
        await recorder.resume()
        await recorder.resume()
        capture_rig.state = f"after-{index}"
        await recorder.pause()
        await recorder.pause()
        assert len(capture_rig.contexts) == index + 1
        assert all(context.closed for context in capture_rig.contexts)
        capture_rig.state = "idle hours"
        assert not (path / "gameplay.webm").exists()
    output = await recorder.close()
    assert output == path / "gameplay.webm"
    assert output.read_text().splitlines() == [
        frame for index in range(3) for frame in (f"before-{index}", f"after-{index}")]
    assert await recorder.close() == output
    assert capture_rig.closed and capture_rig.stopped
    assert not list(path.glob(".video-*"))


async def test_video_close_finalizes_active_segment_without_ffmpeg_join(tmp_path, capture_rig):
    recorder = VideoRecorder("http://localhost/#watch-MiBeBot", tmp_path)
    await recorder.start()
    capture_rig.state = "last action"
    output = await recorder.close()
    assert output.read_text().splitlines() == ["initial", "last action"]
    assert capture_rig.process is None
    assert capture_rig.closed and capture_rig.stopped
    with pytest.raises(RuntimeError, match="not available"):
        await recorder.resume()


async def test_video_join_failure_keeps_all_finalized_segments(tmp_path, capture_rig):
    recorder = VideoRecorder("http://localhost/#watch-MiBeBot", tmp_path)
    await recorder.start()
    await recorder.pause()
    capture_rig.state = "second"
    await recorder.resume()
    capture_rig.join_error = True
    with pytest.raises(RuntimeError, match="invalid segment"):
        await recorder.close()
    segments = sorted(tmp_path.glob(".video-*/segment-*.webm"))
    assert [segment.read_text().splitlines() for segment in segments] == [
        ["initial", "initial"], ["second", "second"]]
    assert not (tmp_path / "gameplay.webm").exists()
    assert capture_rig.closed and capture_rig.stopped
    with pytest.raises(RuntimeError, match="invalid segment"):
        await recorder.close()


async def test_video_pause_failure_releases_capture_and_preserves_usable_prefix(tmp_path, capture_rig):
    recorder = VideoRecorder("http://localhost/#watch-MiBeBot", tmp_path)
    await recorder.start()
    await recorder.pause()
    capture_rig.state = "partial second segment"
    await recorder.resume()
    capture_rig.paint_error = RuntimeError("spectator disconnected")
    with pytest.raises(RuntimeError, match="spectator disconnected"):
        await recorder.pause()
    assert capture_rig.closed and capture_rig.stopped
    assert all(context.closed for context in capture_rig.contexts)
    with pytest.raises(RuntimeError, match="not available"):
        await recorder.resume()
    output = await recorder.close()
    assert output.read_text().splitlines() == ["initial", "initial"]
    assert len(list(tmp_path.glob(".video-*/segment-*.webm"))) == 1
    assert next(tmp_path.glob(".video-*/browser-*.webm")).read_text() == "partial second segment\n"


async def test_video_cancelled_start_releases_browser_and_retains_raw_capture(tmp_path, capture_rig):
    capture_rig.navigation_gate = asyncio.Event()
    recorder = VideoRecorder("http://localhost/#watch-MiBeBot", tmp_path)
    task = asyncio.create_task(recorder.start())
    await capture_rig.navigation_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert capture_rig.closed and capture_rig.stopped
    assert capture_rig.contexts[0].closed
    assert await recorder.close() is None
    assert len(list(tmp_path.glob(".video-*/browser-*.webm"))) == 1


async def test_video_cancelled_join_kills_process_and_keeps_segments(tmp_path, capture_rig):
    recorder = VideoRecorder("http://localhost/#watch-MiBeBot", tmp_path)
    await recorder.start()
    await recorder.pause()
    await recorder.resume()
    await recorder.pause()
    capture_rig.join_gate = asyncio.Event()
    task = asyncio.create_task(recorder.close())
    await capture_rig.join_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert capture_rig.process.killed
    assert capture_rig.closed and capture_rig.stopped
    assert len(list(tmp_path.glob(".video-*/segment-*.webm"))) == 2
    assert not (tmp_path / "gameplay.webm").exists()


async def test_video_cancelled_pause_retains_completed_and_partial_segments(tmp_path, capture_rig):
    recorder = VideoRecorder("http://localhost/#watch-MiBeBot", tmp_path)
    await recorder.start()
    await recorder.pause()
    capture_rig.state = "second segment"
    await recorder.resume()
    task = asyncio.create_task(recorder.pause())
    await asyncio.sleep(0)  # Cancel while waiting for the final spectator paint.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert capture_rig.closed and capture_rig.stopped
    assert all(context.closed for context in capture_rig.contexts)
    output = await recorder.close()
    assert output.read_text().splitlines() == ["initial", "initial"]
    assert len(list(tmp_path.glob(".video-*/segment-*.webm"))) == 1
    assert next(tmp_path.glob(".video-*/browser-*.webm")).read_text() == "second segment\n"


def test_native_final_smite_is_not_misattributed_to_an_earlier_melee_hit(tmp_path):
    recorder = Recorder(tmp_path,{})
    dead = observation(hp=-5,dead=True,game_over=True,messages=(
        "The orc hits you with a +0 whip.","Beogh smites you!","You die..."))
    result = recorder.finish("dead",dead)
    recorder.close()
    assert result["death_cause"] == "Beogh smites you!"
    assert result["death_cause_source"] == "last_damage_message"
