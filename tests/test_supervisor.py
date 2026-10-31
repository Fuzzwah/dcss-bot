import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import stat

import pytest

from dcss_bot.config import Config
from dcss_bot.models import Action, Monster, Observation
from dcss_bot.supervisor import Supervisor


class MemoryRecorder:
    def __init__(self, path):
        self.path = path
        self.events = []
        self.records = []
        self.statuses = []
        self.changed = asyncio.Event()
        self.closed = False
        self.summary = None

    def event(self, kind, data):
        self.events.append((kind, data))

    def record(self, before, action, after, elapsed):
        self.records.append((before, action, after, elapsed))

    def publish_status(self, status):
        self.statuses.append(status)
        self.changed.set()

    def finish(self, result, observation, details=None):
        self.summary = {"result": result, "observation": observation.to_dict(),
                        "run_dir": str(self.path), "details": details}
        return self.summary

    def close(self):
        self.closed = True

    async def wait_status(self, predicate):
        async def wait():
            while True:
                self.changed.clear()
                if self.statuses and predicate(self.statuses[-1]):
                    return self.statuses[-1]
                await self.changed.wait()
        return await asyncio.wait_for(wait(), 2)


class FakeAdapter:
    def __init__(self, observation=None, effect=None):
        self.obs = observation or Observation(name="Bot", hp=20, hp_max=20, place="D",
                                              depth=1, input_mode=1)
        self.state = self
        self.end_reason = ""
        self.effect = effect
        self.commands = []
        self.keys = []
        self.connects = 0
        self.creations = 0
        self.reconnects = 0
        self.closed = False
        self.active = 0
        self.receiving = False
        self.started = asyncio.Event()
        self.release = None
        self.incoming = asyncio.Queue()
        self.ping_received = asyncio.Event()
        self.save_prompt = "Save game and exit?"

    def observation(self):
        return self.obs

    async def connect(self):
        self.connects += 1
        return self.obs

    async def create_character(self):
        self.creations += 1
        return self.obs

    async def reconnect(self):
        assert not self.receiving and not self.active
        self.reconnects += 1
        return self.obs

    def housekeeping(self, observation):
        if observation.input_mode == 5:
            return Action("more", reason="Message page", objective="interface")
        if observation.input_mode == 7:
            return Action("pause", reason="Unknown prompt")
        return None

    async def dispatch(self, action):
        assert not self.receiving and not self.active
        self.active += 1
        self.commands.append(action)
        try:
            if action.kind == "save":
                self.obs = replace(self.obs, input_mode=8, prompt=self.save_prompt)
            elif action.kind == "confirm":
                self.obs = replace(self.obs, input_mode=1, prompt="")
            else:
                self.started.set()
                if self.release is not None:
                    await self.release.wait()
                if self.effect is not None:
                    self.obs = self.effect(self.obs, action)
                elif action.kind == "more":
                    self.obs = replace(self.obs, input_mode=1)
                else:
                    self.obs = replace(self.obs, turn=self.obs.turn + 1,
                                       position=(self.obs.position[0] + 1, 0))
            return self.obs
        finally:
            self.active -= 1

    async def receive(self, timeout=None):
        assert not self.active and not self.receiving
        self.receiving = True
        try:
            item = await asyncio.wait_for(self.incoming.get(), timeout)
            if item == "ping":
                self.ping_received.set()
            elif isinstance(item, Exception):
                raise item
            else:
                self.obs = item
            return []
        finally:
            self.receiving = False

    async def key(self, key):
        assert self.obs.dead or self.obs.game_over
        self.keys.append(key)
        # A terminal acknowledgement can expose a new-game UI. Never act on it.
        self.obs = replace(self.obs, dead=False, game_over=False, input_mode=1,
                           menu={"type": "newgame-choice"})
        return self.obs

    async def close(self):
        self.closed = True


class FakeAgent:
    def __init__(self, action=None, error=None):
        self.action = action or Action("move", "l", "Open corridor", "Explore")
        self.error = error
        self.calls = 0
        self.started = asyncio.Event()
        self.release = None

    async def decide(self, observation):
        self.calls += 1
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.action


class StatefulVideo:
    """Capture labelled frames only while a segment is active."""

    def __init__(self):
        self.active = False
        self.started = False
        self.closed = False
        self.busy = False
        self.segments = []
        self.entered = {phase: asyncio.Event() for phase in ("start", "pause", "resume", "close")}
        self.holds = {}
        self.failure = None

    async def _transition(self, phase):
        assert not self.busy, "Concurrent video operations"
        assert not self.closed, "Disabled recording was reused"
        self.busy = True
        self.entered[phase].set()
        try:
            if phase in self.holds:
                await self.holds[phase].wait()
            if phase == self.failure:
                raise RuntimeError(f"{phase} capture failed")
        finally:
            self.busy = False

    def _activate(self):
        if not self.active:
            self.segments.append([])
            self.active = True

    def capture(self, frame):
        if self.active:
            self.segments[-1].append(frame)

    async def start(self, *, paused=False):
        await self._transition("start")
        self.started = True
        if not paused:
            self._activate()

    async def pause(self):
        if self.active:
            await self._transition("pause")
            self.active = False

    async def resume(self):
        if not self.active:
            await self._transition("resume")
            self._activate()

    async def close(self):
        try:
            await self._transition("close")
        finally:
            self.active = False
            self.closed = True
        return self.path / "gameplay.webm" if self.segments else None


@pytest.fixture
def video(monkeypatch):
    capture = StatefulVideo()
    def create(url, path):
        capture.url, capture.path = url, path
        return capture
    monkeypatch.setattr("dcss_bot.supervisor.VideoRecorder", create)
    return capture


def make_supervisor(tmp_path, adapter=None, agent=None, **config):
    recorder = MemoryRecorder(tmp_path / "run")
    settings = Config(control_socket=tmp_path / "control.sock", delay=0, timeout=0.5, **config)
    supervisor = Supervisor(settings, agent or FakeAgent(), adapter or FakeAdapter(), recorder)
    return supervisor


@asynccontextmanager
async def running(supervisor):
    task = asyncio.create_task(supervisor.run())
    try:
        await supervisor.recorder.wait_status(lambda s: s["ready"])
        yield task
    finally:
        if not task.done():
            supervisor.request_stop()
            for component in (supervisor.agent, supervisor.adapter):
                if getattr(component, "release", None) is not None:
                    component.release.set()
        await asyncio.wait_for(task, 2)


async def command(supervisor, name, **values):
    reader, writer = await asyncio.open_unix_connection(str(supervisor.config.control_socket))
    try:
        writer.write((json.dumps({"command": name, **values}) + "\n").encode())
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 2))
    finally:
        writer.close()
        await writer.wait_closed()


async def wait_requested_pause(supervisor):
    # A status reply is independent of the active action boundary.
    for _ in range(30):
        if (await command(supervisor, "status"))["paused"]:
            return
    pytest.fail("Pause request did not close the action gate")


async def test_initial_pause_private_controls_ping_delay_and_stop(tmp_path):
    supervisor = make_supervisor(tmp_path, paused=True)
    async with running(supervisor) as task:
        path = supervisor.config.control_socket
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.with_name(path.name + ".lock").stat().st_mode) == 0o600
        status = await command(supervisor, "status")
        assert status["state"] == "paused" and status["actions"] == 0
        assert (await command(supervisor, "delay", value=0.1))["delay"] == 0.1
        for value in (-1, 61, True, "1", float("nan")):
            assert not (await command(supervisor, "delay", value=value))["ok"]
        assert not (await command(supervisor, "explode"))["ok"]
        await supervisor.adapter.incoming.put("ping")
        await asyncio.wait_for(supervisor.adapter.ping_received.wait(), 1)
        assert supervisor.agent.calls == 0
        response = await command(supervisor, "stop")
        assert response["stopping"]
        summary = await asyncio.wait_for(task, 1)
    assert summary["result"] == "stopped"
    assert [a.kind for a in supervisor.adapter.commands] == ["save", "confirm"]
    assert supervisor.adapter.closed and supervisor.recorder.closed
    assert not path.exists() and not path.with_name(path.name + ".lock").exists()


async def test_single_step_waits_for_action_then_remains_paused(tmp_path):
    adapter = FakeAdapter()
    adapter.release = asyncio.Event()
    supervisor = make_supervisor(tmp_path, adapter=adapter, paused=True)
    async with running(supervisor):
        step = asyncio.create_task(command(supervisor, "step"))
        await asyncio.wait_for(adapter.started.wait(), 1)
        assert not step.done()
        status = await command(supervisor, "status")
        assert status["paused"] and status["actions"] == 1
        adapter.release.set()
        reply = await step
        assert reply["step_completed"] and reply["state"] == "paused"
        for _ in range(3):
            assert (await command(supervisor, "status"))["actions"] == 1
        await command(supervisor, "stop")
    assert supervisor.agent.calls == 1


async def test_pause_acknowledges_inflight_boundary_without_next_action(tmp_path):
    adapter = FakeAdapter()
    adapter.release = asyncio.Event()
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    async with running(supervisor):
        await asyncio.wait_for(adapter.started.wait(), 1)
        pause = asyncio.create_task(command(supervisor, "pause"))
        await wait_requested_pause(supervisor)
        assert not pause.done()
        adapter.release.set()
        reply = await pause
        assert reply["state"] == "paused" and reply["actions"] == 1
        assert (await command(supervisor, "status"))["actions"] == 1
        await command(supervisor, "stop")
    assert len(supervisor.recorder.records) == 1


async def test_pause_during_decision_prevents_unsent_action(tmp_path, video):
    agent = FakeAgent()
    agent.release = asyncio.Event()
    supervisor = make_supervisor(tmp_path, agent=agent, record_video=True)
    async with running(supervisor):
        await asyncio.wait_for(agent.started.wait(), 1)
        pause = asyncio.create_task(command(supervisor, "pause"))
        await wait_requested_pause(supervisor)
        assert video.active and not pause.done()
        agent.release.set()
        assert (await pause)["state"] == "paused"
        assert not video.active
        video.capture("paused after decision")
        assert supervisor.adapter.commands == []
        await command(supervisor, "stop")
    assert video.segments == [[]]


async def test_resume_runs_until_action_limit(tmp_path):
    supervisor = make_supervisor(tmp_path, paused=True, max_actions=2)
    async with running(supervisor) as task:
        assert (await command(supervisor, "resume"))["ok"]
        summary = await asyncio.wait_for(task, 1)
    assert summary["result"] == "action_limit"
    assert len(supervisor.recorder.records) == 2
    assert [a.kind for a in supervisor.adapter.commands][-2:] == ["save", "confirm"]


async def test_stop_interrupts_long_pacing_wait(tmp_path):
    supervisor = make_supervisor(tmp_path, paused=True)
    async with running(supervisor) as task:
        await command(supervisor, "delay", value=60)
        await command(supervisor, "resume")
        await supervisor.recorder.wait_status(lambda s: s["actions"] == 1 and s["state"] == "running")
        await command(supervisor, "stop")
        summary = await asyncio.wait_for(task, 1)
    assert summary["result"] == "stopped"
    assert len(supervisor.recorder.records) == 1


async def test_turn_limit_is_relative_to_resumed_character(tmp_path):
    adapter = FakeAdapter(Observation(hp=20, hp_max=20, input_mode=1, turn=182))
    supervisor = make_supervisor(tmp_path, adapter=adapter, max_turns=3)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "turn_limit"
    assert summary["observation"]["turn"] == 185
    assert summary["details"]["turns_played"] == 3
    assert len(supervisor.recorder.records) == 3


@pytest.mark.parametrize("cycling", [False, True])
async def test_no_progress_and_meaningful_cycles_pause(tmp_path, cycling):
    def effect(obs, action):
        if cycling:
            return replace(obs, turn=obs.turn + 1, position=(1 - obs.position[0], 0))
        return obs
    supervisor = make_supervisor(tmp_path, adapter=FakeAdapter(effect=effect), stall_limit=6)
    async with running(supervisor):
        status = await supervisor.recorder.wait_status(lambda s: s["state"] == "paused")
        assert status["actions"] == 6
        assert (await command(supervisor, "status"))["actions"] == 6
        await command(supervisor, "stop")


async def test_monster_transport_identity_churn_cannot_hide_an_action_cycle(tmp_path):
    initial = Observation(hp=20, hp_max=20, input_mode=1,
                          monsters=(Monster(1, "dart slug", 3, 0),))
    def effect(obs, action):
        return replace(obs, turn=obs.turn + 1,
                       monsters=(replace(obs.monsters[0], id=obs.monsters[0].id + 1),))
    supervisor = make_supervisor(tmp_path, adapter=FakeAdapter(initial, effect=effect), stall_limit=6)
    async with running(supervisor):
        status = await supervisor.recorder.wait_status(lambda s: s["state"] == "paused")
        assert status["actions"] == 6
        assert supervisor.observation.turn == 6
        await command(supervisor, "stop")


async def test_death_recording_preserves_terminal_snapshot_and_only_drains_more(tmp_path):
    def die(obs, action):
        return replace(obs, turn=obs.turn + 1, hp=0, dead=True, game_over=True,
                       input_mode=5, messages=("The ogre hits you!", "You die..."))
    adapter = FakeAdapter(effect=die)
    adapter.end_reason = "Slain by an ogre"
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "dead"
    assert summary["observation"]["dead"] and summary["observation"]["hp"] == 0
    assert summary["details"]["end_reason"] == "Slain by an ogre"
    assert supervisor.recorder.statuses[-1]["state"] == "dead"
    assert adapter.keys == [" "] and adapter.creations == 1
    assert [a.kind for a in adapter.commands] == ["move"]
    assert supervisor.recorder.records[0][2].dead


async def test_existing_game_over_never_creates_character(tmp_path):
    adapter = FakeAdapter(Observation(game_over=True))
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "game_over"
    assert adapter.creations == 0 and adapter.commands == []


@pytest.mark.parametrize("failure", ["exception", "timeout", "unknown"])
async def test_strategy_failures_pause_log_and_allow_clean_stop(tmp_path, failure):
    agent = FakeAgent(error=RuntimeError("strategy broke") if failure == "exception" else None)
    if failure == "timeout":
        agent.release = asyncio.Event()
    elif failure == "unknown":
        agent.action = {"kind": "quit"}
    supervisor = make_supervisor(tmp_path, agent=agent)
    async with running(supervisor):
        status = await supervisor.recorder.wait_status(lambda s: s["state"] == "paused")
        assert "Strategy failed" in status["reason"] and status["actions"] == 0
        assert any(kind == "strategy_error" for kind, _ in supervisor.recorder.events)
        assert supervisor.adapter.commands == []
        await command(supervisor, "stop")
    assert supervisor.recorder.closed


async def test_housekeeping_precedes_strategy_and_unknown_prompt_pauses(tmp_path):
    adapter = FakeAdapter(Observation(hp=20, hp_max=20, input_mode=5))
    supervisor = make_supervisor(tmp_path, adapter=adapter, max_actions=2)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "action_limit"
    assert [a.kind for a in adapter.commands][:2] == ["more", "move"]
    assert supervisor.agent.calls == 1

    adapter = FakeAdapter(Observation(hp=20, hp_max=20, input_mode=7, prompt="Unknown?"))
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    async with running(supervisor):
        await supervisor.recorder.wait_status(lambda s: s["state"] == "paused")
        assert supervisor.agent.calls == 0 and adapter.commands == []
        await command(supervisor, "stop")
    assert adapter.commands == []  # No save key typed into an unknown prompt.


async def test_transport_failure_reconnects_without_replaying_ambiguous_action(tmp_path):
    adapter = FakeAdapter()
    def interrupted(obs, action):
        adapter.obs = replace(obs, turn=obs.turn + 1, position=(1, 0))
        raise OSError("transport lost after key was sent")
    adapter.effect = interrupted
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    async with running(supervisor):
        status = await supervisor.recorder.wait_status(lambda s: s["state"] == "paused")
        assert "no action replayed" in status["reason"]
        assert adapter.reconnects == 1 and status["turn"] == 1
        assert [a.kind for a in adapter.commands] == ["move"]
        assert any(s["state"] == "disconnected" for s in supervisor.recorder.statuses)
        assert any(kind == "action_uncertain" for kind, _ in supervisor.recorder.events)
        await command(supervisor, "stop")
    assert supervisor.recorder.records == []


async def test_adapter_timeout_is_bounded_and_does_not_replay(tmp_path):
    adapter = FakeAdapter()
    adapter.release = asyncio.Event()
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    async with running(supervisor):
        status = await supervisor.recorder.wait_status(lambda s: s["state"] == "paused")
        assert status["actions"] == 1 and adapter.reconnects == 1
        assert adapter.active == 0
        await command(supervisor, "stop")
    assert [a.kind for a in adapter.commands].count("move") == 1


@pytest.mark.parametrize("collision", ["control.sock", "control.sock.lock"])
async def test_socket_collision_never_removes_foreign_path(tmp_path, collision):
    foreign = tmp_path / collision
    foreign.write_text("not ours")
    supervisor = make_supervisor(tmp_path)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "error"
    assert foreign.read_text() == "not ours"
    assert supervisor.adapter.connects == 0 and supervisor.recorder.closed
    if collision == "control.sock":
        assert not (tmp_path / "control.sock.lock").exists()


async def test_unknown_save_confirmation_is_not_accepted(tmp_path):
    adapter = FakeAdapter()
    adapter.save_prompt = "Really attack this friendly creature?"
    supervisor = make_supervisor(tmp_path, adapter=adapter, max_actions=1)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["details"]["save"] == "disconnect"
    assert [a.kind for a in adapter.commands] == ["move", "save"]


async def test_video_failure_does_not_prevent_play_or_cleanup(tmp_path, monkeypatch):
    videos = []
    class BrokenVideo:
        def __init__(self, url, path):
            self.url, self.path, self.closed = url, path, False
            videos.append(self)

        async def start(self, *, paused=False):
            raise RuntimeError("Chromium not installed")

        async def pause(self):
            pytest.fail("Failed recording cannot be paused")

        async def resume(self):
            pytest.fail("Failed recording cannot be resumed")

        async def close(self):
            self.closed = True
            return None

    monkeypatch.setattr("dcss_bot.supervisor.VideoRecorder", BrokenVideo)
    supervisor = make_supervisor(tmp_path, max_actions=1, record_video=True)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "action_limit" and len(supervisor.recorder.records) == 1
    assert videos[0].url == "http://127.0.0.1:8080/#watch-MiBeBot"
    assert videos[0].path == supervisor.recorder.path and videos[0].closed
    assert any(kind == "video_error" for kind, _ in supervisor.recorder.events)


async def test_stop_closes_menu_before_saving_without_selecting_an_option(tmp_path):
    adapter = FakeAdapter(Observation(hp=20, hp_max=20, input_mode=1,
                                      menu={"type": "inventory"}))
    adapter.effect = lambda obs, action: replace(obs, menu={})
    supervisor = make_supervisor(tmp_path, adapter=adapter, paused=True)
    async with running(supervisor):
        await command(supervisor, "stop")
    assert [action.kind for action in adapter.commands] == ["cancel", "save", "confirm"]
    assert supervisor.recorder.summary["details"]["save"] == "saved"


async def test_uncancellable_menu_shutdown_is_bounded(tmp_path):
    adapter = FakeAdapter(Observation(hp=20, hp_max=20, input_mode=1,
                                      menu={"type": "inventory"}), effect=lambda obs, action: obs)
    supervisor = make_supervisor(tmp_path, adapter=adapter, paused=True)
    async with running(supervisor):
        await command(supervisor, "stop")
    assert [action.kind for action in adapter.commands] == ["cancel"] * 3
    assert supervisor.recorder.summary["details"]["save"] == "disconnect"


async def test_reconnect_exhaustion_records_error_and_cleans_up(tmp_path):
    adapter = FakeAdapter()
    def interrupted(obs, action):
        raise OSError("connection lost")
    async def unavailable():
        adapter.reconnects += 1
        raise OSError("server unavailable")
    adapter.effect = interrupted
    adapter.reconnect = unavailable
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "error" and summary["state"] == "error"
    assert summary["details"]["error"] == "server unavailable"
    assert adapter.reconnects == 1 and [action.kind for action in adapter.commands] == ["move"]
    assert adapter.closed and supervisor.recorder.closed
    assert not supervisor.config.control_socket.exists()


async def test_terminal_acknowledgement_loop_is_bounded(tmp_path):
    adapter = FakeAdapter(Observation(hp=0, hp_max=20, dead=True, input_mode=5))
    async def endless_more(key):
        adapter.keys.append(key)
        return adapter.obs
    adapter.key = endless_more
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "dead"
    assert adapter.keys == [" "] * 8
    assert adapter.creations == 0 and adapter.commands == []
    assert any(kind == "terminal_drain_limit" for kind, _ in supervisor.recorder.events)


async def test_death_seen_before_transport_loss_is_not_erased_by_reconnect(tmp_path):
    adapter = FakeAdapter()
    def die_and_disconnect(obs, action):
        adapter.obs = replace(obs, hp=0, dead=True, messages=("You die...",))
        raise OSError("connection closed after terminal update")
    adapter.effect = die_and_disconnect
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "dead" and summary["observation"]["dead"]
    assert adapter.reconnects == 0 and adapter.creations == 1


async def test_reconnect_to_new_character_menu_cannot_automatically_start_again(tmp_path):
    adapter = FakeAdapter()
    def disconnected(obs, action):
        raise OSError("connection lost")
    async def new_character_menu():
        adapter.reconnects += 1
        adapter.obs = replace(adapter.obs, menu={"type": "newgame-choice"})
        return adapter.obs
    adapter.effect = disconnected
    adapter.reconnect = new_character_menu
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    async with running(supervisor):
        await supervisor.recorder.wait_status(lambda s: s["state"] == "paused")
        await command(supervisor, "resume")
        status = await supervisor.recorder.wait_status(
            lambda s: s["state"] == "paused" and "new run explicitly" in s["reason"])
        assert status["actions"] == 1
        await command(supervisor, "stop")
    assert adapter.creations == 1 and [action.kind for action in adapter.commands] == ["move"]


async def test_terminal_drain_retains_late_death_reason(tmp_path):
    adapter = FakeAdapter(Observation(hp=0, hp_max=20, dead=True, input_mode=5))
    original_key = adapter.key
    async def show_game_over(key):
        adapter.end_reason = "Slain by an adder"
        return await original_key(key)
    adapter.key = show_game_over
    supervisor = make_supervisor(tmp_path, adapter=adapter)
    summary = await asyncio.wait_for(supervisor.run(), 1)
    assert summary["result"] == "dead"
    assert summary["details"]["end_reason"] == "Slain by an adder"
    assert summary["observation"]["dead"]


async def test_unattended_pause_saves_and_terminates_without_another_action(tmp_path):
    supervisor = make_supervisor(tmp_path, agent=FakeAgent(Action("pause", reason="Unsafe unknown situation")))
    summary = await asyncio.wait_for(supervisor.run(stop_on_pause=True), 1)
    assert summary["result"] == "paused"
    assert summary["details"]["pause_reason"] == "Unsafe unknown situation"
    assert summary["details"]["save"] == "saved"
    assert [action.kind for action in supervisor.adapter.commands] == ["save", "confirm"]
    assert supervisor.recorder.closed and not supervisor.config.control_socket.exists()


async def test_hidden_recovery_timer_can_outlast_cycle_window_then_expire(tmp_path):
    def recovery(obs, action):
        turn = obs.turn + 1
        return replace(obs, turn=turn, time=turn * 10, statuses=("Slow",) if turn < 40 else ())
    adapter = FakeAdapter(Observation(hp=20, hp_max=20, input_mode=1, statuses=("Slow",)), effect=recovery)
    supervisor = make_supervisor(tmp_path, adapter=adapter, agent=FakeAgent(Action("wait")), max_actions=45)
    summary = await asyncio.wait_for(supervisor.run(stop_on_pause=True), 2)
    assert summary["result"] == "action_limit"
    assert summary["observation"]["turn"] == 45 and not summary["observation"]["statuses"]


async def test_recovery_without_visible_improvement_still_has_a_finite_bound(tmp_path):
    adapter = FakeAdapter(Observation(hp=20, hp_max=20, input_mode=1, statuses=("Slow",)),
                          effect=lambda obs, action: replace(obs, turn=obs.turn + 1, time=obs.time + 10))
    supervisor = make_supervisor(tmp_path, adapter=adapter, agent=FakeAgent(Action("wait")))
    summary = await asyncio.wait_for(supervisor.run(stop_on_pause=True), 2)
    assert summary["result"] == "paused"
    assert summary["observation"]["turn"] == 120
    assert "Recovery exceeded" in summary["details"]["pause_reason"]


async def test_initially_paused_video_never_captures_idle_or_save(tmp_path, video):
    supervisor = make_supervisor(tmp_path, paused=True, record_video=True)
    async with running(supervisor) as task:
        assert video.started and not video.active
        video.capture("idle")
        await supervisor.adapter.incoming.put("ping")
        await asyncio.wait_for(supervisor.adapter.ping_received.wait(), 1)
        assert (await command(supervisor, "pause"))["state"] == "paused"
        video.capture("still idle")
        await command(supervisor, "stop")
        summary = await task
    assert video.closed and video.segments == []
    assert "video" not in summary["details"]
    assert [action.kind for action in supervisor.adapter.commands] == ["save", "confirm"]
    assert supervisor.recorder.records == []


async def test_video_step_captures_one_action_then_finalizes_before_ack(tmp_path, video):
    def advance(obs, action):
        video.capture(action.kind)
        return replace(obs, turn=obs.turn + 1, position=(obs.turn + 1, 0))
    adapter = FakeAdapter(effect=advance)
    adapter.release = asyncio.Event()
    video.holds["pause"] = asyncio.Event()
    supervisor = make_supervisor(tmp_path, adapter=adapter, paused=True, record_video=True)
    async with running(supervisor):
        step = asyncio.create_task(command(supervisor, "step"))
        await asyncio.wait_for(adapter.started.wait(), 1)
        assert video.active and not step.done()
        adapter.release.set()
        await asyncio.wait_for(video.entered["pause"].wait(), 1)
        assert not step.done()
        assert (await command(supervisor, "status"))["actions"] == 1
        video.holds["pause"].set()
        reply = await step
        assert reply["step_completed"] and reply["state"] == "paused"
        assert not video.active
        video.capture("idle between steps")
        assert (await command(supervisor, "step"))["step_completed"]
        assert not video.active
        video.capture("idle after steps")
        await command(supervisor, "stop")
    assert video.segments == [["move"], ["move"]]
    assert len(supervisor.recorder.records) == 2
    assert supervisor.recorder.summary["details"]["video"].endswith("/gameplay.webm")


async def test_video_pause_waits_for_action_and_resume_precedes_next_action(tmp_path, video):
    def advance(obs, action):
        video.capture(action.kind)
        return replace(obs, turn=obs.turn + 1, position=(obs.turn + 1, 0))
    adapter = FakeAdapter(effect=advance)
    adapter.release = asyncio.Event()
    video.holds["pause"] = asyncio.Event()
    video.holds["resume"] = asyncio.Event()
    supervisor = make_supervisor(tmp_path, adapter=adapter, record_video=True)
    async with running(supervisor):
        await asyncio.wait_for(adapter.started.wait(), 1)
        pause = asyncio.create_task(command(supervisor, "pause"))
        await wait_requested_pause(supervisor)
        assert video.active and not pause.done()
        assert not video.entered["pause"].is_set()
        adapter.release.set()
        await asyncio.wait_for(video.entered["pause"].wait(), 1)
        assert not pause.done() and video.segments == [["move"]]
        assert (await command(supervisor, "status"))["actions"] == 1
        video.holds["pause"].set()
        assert (await pause)["state"] == "paused"
        assert not video.active
        video.capture("paused")
        adapter.started.clear()
        adapter.release.clear()
        resume = asyncio.create_task(command(supervisor, "resume"))
        await asyncio.wait_for(video.entered["resume"].wait(), 1)
        assert not adapter.started.is_set() and not resume.done()
        assert (await command(supervisor, "status"))["actions"] == 1
        video.capture("waiting for capture")
        video.holds["resume"].set()
        assert (await resume)["ok"]
        await asyncio.wait_for(adapter.started.wait(), 1)
        assert video.active
        pause = asyncio.create_task(command(supervisor, "pause"))
        await wait_requested_pause(supervisor)
        adapter.release.set()
        await pause
        video.capture("paused again")
        await command(supervisor, "stop")
    assert video.segments == [["move"], ["move"]]
    assert len(supervisor.recorder.records) == 2
    assert [kind for kind, _ in supervisor.recorder.events if kind.startswith("video_")] == [
        "video_started", "video_paused", "video_resumed", "video_paused", "video_closed"]


async def test_pause_during_video_start_closes_capture_before_ack(tmp_path, video):
    video.holds["start"] = asyncio.Event()
    supervisor = make_supervisor(tmp_path, record_video=True)
    task = asyncio.create_task(supervisor.run())
    try:
        await asyncio.wait_for(video.entered["start"].wait(), 1)
        pause = asyncio.create_task(command(supervisor, "pause"))
        await wait_requested_pause(supervisor)
        video.holds["start"].set()
        assert (await pause)["state"] == "paused"
        assert video.started and not video.active
        video.capture("paused after startup")
        assert supervisor.adapter.commands == []
        await command(supervisor, "stop")
    finally:
        video.holds["start"].set()
        supervisor.request_stop()
        await asyncio.wait_for(task, 2)
    assert video.segments == [[]] and video.closed


@pytest.mark.parametrize("cause,expected_actions", [
    ("agent", 0), ("strategy", 0), ("action", 1), ("unknown_prompt", 0),
    ("new_character", 0), ("stall", 4), ("cycle", 4), ("recovery", 120),
])
async def test_automatic_pauses_stop_video_before_idle(tmp_path, video, cause, expected_actions):
    agent = FakeAgent()
    adapter = FakeAdapter()
    if cause == "agent":
        agent.action = Action("pause", reason="Unsafe situation")
    elif cause == "strategy":
        agent.error = ValueError("Bad strategy")
    elif cause == "unknown_prompt":
        adapter.obs = replace(adapter.obs, input_mode=7, prompt="Unknown?")
    elif cause == "new_character":
        adapter.obs = replace(adapter.obs, menu={"type": "newgame-choice"})
    elif cause == "recovery":
        agent.action = Action("wait")
        adapter.obs = replace(adapter.obs, statuses=("Slow",))
    def effect(obs, action):
        video.capture(action.kind)
        if cause == "action":
            raise ValueError("Bad action")
        if cause == "cycle":
            return replace(obs, turn=obs.turn + 1, position=(1 - obs.position[0], 0))
        if cause == "recovery":
            return replace(obs, turn=obs.turn + 1, time=obs.time + 10)
        return obs
    adapter.effect = effect
    supervisor = make_supervisor(tmp_path, adapter=adapter, agent=agent, stall_limit=4, record_video=True)
    async with running(supervisor):
        status = await supervisor.recorder.wait_status(lambda s: s["state"] == "paused")
        assert status["actions"] == expected_actions and not video.active
        video.capture("automatic pause idle")
        assert (await command(supervisor, "status"))["actions"] == expected_actions
        await command(supervisor, "stop")
    assert sum(map(len, video.segments)) == expected_actions
    assert video.closed
    assert any(kind == "video_paused" for kind, _ in supervisor.recorder.events)


@pytest.mark.parametrize("during_action", [True, False])
async def test_transport_reconnect_happens_with_video_paused(tmp_path, video, during_action):
    adapter = FakeAdapter()
    reconnect_started, reconnect_release = asyncio.Event(), asyncio.Event()
    def advance(obs, action):
        video.capture(action.kind)
        adapter.obs = replace(obs, turn=obs.turn + 1, position=(1, 0))
        if during_action:
            raise OSError("Connection lost after action")
        return adapter.obs
    async def reconnect():
        assert not adapter.active and not adapter.receiving
        adapter.reconnects += 1
        reconnect_started.set()
        await reconnect_release.wait()
        return adapter.obs
    adapter.effect, adapter.reconnect = advance, reconnect
    supervisor = make_supervisor(tmp_path, adapter=adapter, paused=True, record_video=True)
    async with running(supervisor):
        await command(supervisor, "delay", value=60)
        await command(supervisor, "resume")
        if not during_action:
            await supervisor.recorder.wait_status(lambda s: s["state"] == "running" and s["actions"] == 1)
            await adapter.incoming.put(OSError("Idle transport lost"))
        await asyncio.wait_for(reconnect_started.wait(), 1)
        assert not video.active
        video.capture("reconnecting")
        assert (await command(supervisor, "status"))["state"] == "reconnecting"
        reconnect_release.set()
        await supervisor.recorder.wait_status(lambda s: s["state"] == "paused")
        video.capture("reconnected but paused")
        await command(supervisor, "stop")
    assert video.segments == [["move"]]
    assert len(supervisor.recorder.records) == (0 if during_action else 1)
    assert adapter.reconnects == 1
    assert [action.kind for action in adapter.commands] == ["move", "save", "confirm"]


@pytest.mark.parametrize("phase", ["start", "pause", "resume", "close"])
@pytest.mark.parametrize("failure", ["exception", "timeout"])
async def test_video_transition_failures_disable_capture_without_stopping_play(
        tmp_path, video, capsys, phase, failure):
    if failure == "exception":
        video.failure = phase
    else:
        video.holds[phase] = asyncio.Event()
    def advance(obs, action):
        video.capture(action.kind)
        return replace(obs, turn=obs.turn + 1, position=(obs.turn + 1, 0))
    supervisor = make_supervisor(tmp_path, adapter=FakeAdapter(effect=advance),
                                 paused=True, record_video=True)
    async with running(supervisor) as task:
        assert (await command(supervisor, "step"))["step_completed"]
        if phase != "close":
            assert video.closed and not video.active
        assert (await command(supervisor, "step"))["step_completed"]
        assert (await command(supervisor, "status"))["state"] == "paused"
        await command(supervisor, "stop")
        summary = await task
    assert summary["result"] == "stopped" and len(supervisor.recorder.records) == 2
    assert [action.kind for action in supervisor.adapter.commands] == ["move", "move", "save", "confirm"]
    if phase == "pause":
        assert summary["details"]["video"] == str(video.path / "gameplay.webm")
    else:
        assert "video" not in summary["details"]
    errors = [data for kind, data in supervisor.recorder.events if kind == "video_error"]
    assert len(errors) == 1 and errors[0]["phase"] == phase
    assert errors[0]["type"] == ("TimeoutError" if failure == "timeout" else "RuntimeError")
    assert "recording disabled" in capsys.readouterr().out
    assert video.closed and not video.active and supervisor.adapter.closed
