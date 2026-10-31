"""Single-flight gameplay with private, boundary-acknowledged local controls."""
import asyncio
from collections import deque
import json
import math
import os
from pathlib import Path
import socket
import time
from urllib.parse import quote, urlsplit, urlunsplit

from websockets.exceptions import ConnectionClosed

from .adapter import WebTilesAdapter
from .config import Config
from .models import Action, Agent, Observation
from .recording import Recorder, VideoRecorder


_TRANSPORT_ERRORS = (OSError, ConnectionClosed, TimeoutError)
_INTERFACE_ACTIONS = {"cancel", "more", "menu", "confirm"}


class Supervisor:
    def __init__(self, config: Config, agent: Agent, adapter: WebTilesAdapter,
                 recorder: Recorder) -> None:
        self.config, self.agent, self.adapter, self.recorder = config, agent, adapter, recorder
        self.observation = Observation()
        self.actions = 0
        self.delay = config.delay
        self.state = "starting"
        self.reason = ""
        self._phase = ""
        self._finished = False
        self._initial_turn = 0
        self.last_action: Action | None = None
        self._paused = config.paused
        self._stopping = False
        self._connected = False
        self._ready = False
        self._steps = 0
        self._step_done: asyncio.Future | None = None
        self._boundary = asyncio.Lock()
        self._commands = asyncio.Lock()
        self._wake = asyncio.Event()
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task] = set()
        self._owned_paths: dict[Path, tuple[int, int]] = {}
        self._video: VideoRecorder | None = None
        self._video_paused = True
        self._video_path: Path | None = None
        self._unchanged = 0
        self._recovery_turns = 0
        self._history: deque = deque(maxlen=max(2, config.stall_limit))
        self._next_action = 0.0

    def request_stop(self) -> None:
        """Signal-handler-safe request; the current action finishes before saving."""
        self._stopping = True
        self._steps = 0
        self._wake.set()

    def status(self) -> dict:
        obs = self.observation
        return {
            "state": self.state, "paused": self._paused, "ready": self._ready,
            "stopping": self._stopping, "actions": self.actions, "turn": obs.turn,
            "location": obs.location, "hp": obs.hp, "hp_max": obs.hp_max, "xl": obs.xl,
            "delay": self.delay, "reason": self.reason or (self.last_action.reason if self.last_action else ""),
            "turns_played": max(0, obs.turn - self._initial_turn),
            "action": self.last_action.to_dict() if self.last_action else None,
            "objective": self.last_action.objective if self.last_action else "",
            "threats": [{"name": m.name, "threat": m.threat} for m in obs.hostiles],
            "run_dir": str(self.recorder.path),
        }

    def _event(self, kind: str, data: dict) -> None:
        self.recorder.event(kind, data)

    def _publish(self, live: bool = False) -> None:
        if not self._finished:
            self.state = ("stopping" if self._stopping else self._phase or
                          ("starting" if not self._ready else "paused" if self._paused else "running"))
        status = self.status()
        self.recorder.publish_status(status)
        if live:
            # JSON keeps prompts/messages on one physical console line.
            print("BOT_STATUS " + json.dumps(status, ensure_ascii=True), flush=True)

    async def _pause(self, reason: str) -> None:
        self._paused = True
        self._steps = 0
        self._phase = ""
        self.reason = reason
        await self._set_video_paused(True)
        self._event("paused", {"reason": reason})
        self._publish(live=True)

    def _complete_step(self, completed: bool) -> None:
        if self._step_done is not None and not self._step_done.done():
            self._step_done.set_result(completed)
        self._step_done = None

    async def _bounded(self, operation):
        return await asyncio.wait_for(operation, timeout=self.config.timeout)

    async def _open_control(self) -> None:
        path = self.config.control_socket
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = path.with_name(path.name + ".lock")
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            stat = os.fstat(fd)
            self._owned_paths[lock_path] = (stat.st_dev, stat.st_ino)
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # bind refuses existing paths, including stale sockets: never steal one.
            old_mask = os.umask(0o177)
            try:
                listener.bind(str(path))
            finally:
                os.umask(old_mask)
            stat = path.lstat()
            self._owned_paths[path] = (stat.st_dev, stat.st_ino)
            listener.setblocking(False)
            self._server = await asyncio.start_unix_server(self._client, sock=listener, limit=4096)
        except BaseException:
            listener.close()
            raise

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._clients.add(task)
        try:
            line = await asyncio.wait_for(reader.readline(), self.config.timeout)
            if not line.endswith(b"\n"):
                raise ValueError("Expected one newline-terminated JSON command")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Control command must be a JSON object")
            response = await self._control(request)
        except (ValueError, TypeError, TimeoutError) as exc:
            response = {**self.status(), "ok": False, "error": str(exc)}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._event("control_error", {"error": str(exc), "type": type(exc).__name__})
            response = {**self.status(), "ok": False, "error": str(exc)}
        else:
            response = {**response, "ok": True}
        finally:
            # The response is sent below so the handler remains registered through drain.
            if task.cancelling():
                writer.close()
                self._clients.discard(task)
        try:
            writer.write((json.dumps(response, allow_nan=False) + "\n").encode())
            await asyncio.wait_for(writer.drain(), self.config.timeout)
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            self._clients.discard(task)

    async def _control(self, request: dict) -> dict:
        command = request.get("command")
        if command == "status":
            return self.status()
        if command not in {"pause", "resume", "step", "stop", "delay"}:
            raise ValueError("Unknown control command")
        step_done = None
        async with self._commands:
            if command == "delay":
                value = request.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("Delay must be a number between 0 and 60")
                if not math.isfinite(value) or not 0 <= value <= 60:
                    raise ValueError("Delay must be a number between 0 and 60")
                self.delay = float(value)
                self._next_action = asyncio.get_running_loop().time() + self.delay
                self._wake.set()
                self._publish()
                return self.status()
            # Set the gate before waiting: the loop cannot start another action.
            if command in {"pause", "step"}:
                self._paused = True
                self._steps = 0
            elif command == "stop":
                self.request_stop()
            self._wake.set()
            async with self._boundary:
                if self._finished or self._stopping:
                    self._complete_step(False)
                    self._publish()
                    return self.status()
                if command == "pause":
                    await self._pause("Paused by local control")
                    self._complete_step(False)
                elif command == "resume":
                    self._complete_step(False)
                    await self._set_video_paused(False)
                    self._paused = False
                    self.reason = ""
                    self._unchanged = 0
                    self._history.clear()
                    self._next_action = 0.0
                elif command == "step":
                    await self._set_video_paused(True)
                    self._complete_step(False)
                    self._steps = 1
                    self.reason = "Single step"
                    step_done = asyncio.get_running_loop().create_future()
                    self._step_done = step_done
                self._publish()
            self._wake.set()
        if step_done is not None:
            completed = await asyncio.shield(step_done)
            return {**self.status(), "step_completed": completed}
        return self.status()

    def _snapshot(self) -> Observation:
        return self.adapter.state.observation()

    async def _reconnect(self, error: Exception) -> None:
        self._connected = False
        self._phase = "disconnected"
        self._publish(live=True)
        self._event("transport_error", {"error": str(error), "type": type(error).__name__})
        await self._set_video_paused(True)
        observed = self._snapshot()
        if observed.dead or observed.game_over:
            self.observation = observed
            return
        self._phase = "reconnecting"
        self._publish()
        self.observation = await self._bounded(self.adapter.reconnect())
        self._connected = True
        await self._pause("Transport reconnected; inspect the observation before resuming (no action replayed)")

    async def _idle(self, duration: float) -> None:
        # Only this loop receives, under the same lock as dispatch. Application pings
        # are serviced even while paused or pacing; no competing recv task exists.
        async with self._boundary:
            if self._stopping or self._wake.is_set():
                self._wake.clear()
                return
            receiving = asyncio.create_task(self.adapter.receive(timeout=duration))
            waking = asyncio.create_task(self._wake.wait())
            try:
                done, _ = await asyncio.wait((receiving, waking), timeout=duration,
                                             return_when=asyncio.FIRST_COMPLETED)
                if receiving in done:
                    await receiving
            except TimeoutError:
                pass
            except _TRANSPORT_ERRORS as exc:
                if not self._stopping:
                    await self._reconnect(exc)
            finally:
                for task in (receiving, waking):
                    if not task.done():
                        task.cancel()
                # A cancelled recv must finish before any next dispatch or receive.
                await asyncio.gather(receiving, waking, return_exceptions=True)
                self._wake.clear()
            self.observation = self._snapshot()
        await asyncio.sleep(0)

    @staticmethod
    def _meaningful(obs: Observation) -> tuple:
        # Wire monster IDs change when creatures leave and re-enter LOS.
        # Identity churn alone must not defeat the repeated-state guard.
        monsters = tuple(sorted((m.name, m.x, m.y, m.threat, m.attitude) for m in obs.monsters))
        return (obs.location, obs.position, obs.hp, obs.hp_max, obs.xl, obs.statuses,
                obs.inventory, monsters, obs.cells, obs.prompt, obs.input_mode,
                json.dumps(obs.menu, sort_keys=True), obs.dead, obs.game_over)

    async def _check_progress(self, before: Observation, action: Action, after: Observation) -> None:
        before_key, after_key = self._meaningful(before), self._meaningful(after)
        if (before.turn, before.time, before_key) == (after.turn, after.time, after_key):
            self._unchanged += 1
        else:
            self._unchanged = 0
        if self._unchanged >= self.config.stall_limit:
            await self._pause(f"No progress after {self._unchanged} actions")
            return
        # Status timers are hidden: healthy post-berserk Slow can legitimately
        # last longer than the ordinary cycle window. Allow bounded, advancing
        # recovery, not arbitrary waits or a policy-supplied objective string.
        recovering = (action.kind == "wait" and not after.hostiles and (
            after.hp < after.hp_max or any(
                status.lower() in {"slow", "berserk", "exh", "exhausted"}
                or "pois" in status.lower() for status in after.statuses)))
        if recovering and after.turn > before.turn:
            self._recovery_turns = self._recovery_turns + after.turn - before.turn if before_key == after_key else 0
            if self._recovery_turns >= 120:
                await self._pause("Recovery exceeded 120 turns without visible improvement")
                return
            self._history.clear()
            return
        self._recovery_turns = 0
        if action.kind in _INTERFACE_ACTIONS:
            return
        self._history.append((action.kind, action.value, after_key))
        if len(self._history) < self._history.maxlen:
            return
        history = list(self._history)
        for period in range(1, len(history) // 2 + 1):
            if all(history[i] == history[i - period] for i in range(period, len(history))):
                await self._pause(f"Repeated action cycle without meaningful progress (period {period})")
                return

    async def _act(self) -> None:
        before = self.observation
        stepping = self._steps > 0
        if stepping:
            self._steps -= 1
        completed = False
        try:
            if stepping:
                await self._set_video_paused(False)
            if before.menu.get("type") == "newgame-choice":
                await self._pause("No active character; start a new run explicitly rather than creating another game")
                return
            self._phase = "deciding"
            self._publish()
            try:
                action = self.adapter.housekeeping(before)
                if action is None:
                    action = await self._bounded(self.agent.decide(before))
                if not isinstance(action, Action):
                    raise ValueError("Agent did not return an Action")
                # Revalidate third-party strategy output at the trust boundary.
                Action(action.kind, action.value, action.reason, action.objective)
            except Exception as exc:
                self._event("strategy_error", {"type": type(exc).__name__, "error": str(exc)})
                await self._pause(f"Strategy failed: {type(exc).__name__}: {exc}")
                return
            # pause/stop arriving during decide must prevent the not-yet-sent action.
            if self._stopping or (self._paused and not stepping):
                return
            self.last_action = action
            if action.kind == "pause":
                await self._pause(action.reason or "Agent requested a pause")
                return
            self.actions += 1
            started = time.monotonic()
            self._phase = "acting"
            self._publish()
            try:
                self.observation = await self._bounded(self.adapter.dispatch(action))
            except _TRANSPORT_ERRORS as exc:
                self._event("action_uncertain", {"action": action.to_dict(), "error": str(exc)})
                await self._reconnect(exc)
                return
            except Exception as exc:
                self.observation = self._snapshot()
                self._event("action_error", {"action": action.to_dict(), "type": type(exc).__name__,
                                             "error": str(exc)})
                await self._pause(f"Action failed: {type(exc).__name__}: {exc}")
                return
            self.recorder.record(before, action, self.observation, time.monotonic() - started)
            completed = True
            if not (self.observation.dead or self.observation.game_over):
                await self._check_progress(before, action, self.observation)
            self._next_action = asyncio.get_running_loop().time() + self.delay
            self._phase = ""
            self._publish(live=True)
        finally:
            if self._paused or self._stopping:
                await self._set_video_paused(True)
            self._phase = ""
            self._publish()
            if stepping:
                self._complete_step(completed)

    def _video_error(self, phase: str, error: Exception) -> None:
        self._event("video_error", {"phase": phase, "error": str(error),
                                    "type": type(error).__name__})
        print(f"BOT_WARNING video {phase} failed; recording disabled: "
              + json.dumps(str(error)), flush=True)

    async def _close_video(self) -> None:
        # Every video operation runs at the action boundary, including cleanup.
        # Detach first so a failed recording is never restarted by later controls.
        video, self._video = self._video, None
        if video is None:
            return
        try:
            self._video_path = await self._bounded(video.close())
            self._event("video_closed", {"path": str(self._video_path) if self._video_path else None})
        except Exception as exc:
            self._video_error("close", exc)

    async def _set_video_paused(self, paused: bool) -> None:
        if self._video is None or self._video_paused == paused:
            return
        phase = "pause" if paused else "resume"
        try:
            await self._bounded(self._video.pause() if paused else self._video.resume())
        except Exception as exc:
            self._video_error(phase, exc)
            await self._close_video()
        else:
            self._video_paused = paused
            self._event("video_paused" if paused else "video_resumed", {})

    async def _start_video(self) -> None:
        if not self.config.record_video:
            return
        url = urlsplit(self.config.url)
        spectator = urlunsplit(("https" if url.scheme == "wss" else "http", url.netloc,
                               "/", "", "watch-" + quote(self.config.username, safe="")))
        try:
            self._video = VideoRecorder(spectator, self.recorder.path)
            paused = self._paused
            await self._bounded(self._video.start(paused=paused))
            self._video_paused = paused
            self._event("video_started", {"paused": paused})
            # A control request can close the gate while browser startup awaits.
            await self._set_video_paused(self._paused or self._stopping)
        except Exception as exc:
            self._video_error("start", exc)
            await self._close_video()

    async def _save(self) -> str:
        obs = self.observation
        if not self._connected or obs.dead or obs.game_over:
            return "not_needed"
        try:
            for _ in range(3):
                if (not obs.menu and obs.input_mode not in {2, 3, 4}) or obs.menu.get("type") == "newgame-choice":
                    break
                obs = await self._bounded(self.adapter.dispatch(Action("cancel", reason="Close interface before saving")))
                self.observation = obs
                self._event("shutdown_acknowledgement", {"kind": "cancel"})
                if obs.dead or obs.game_over:
                    return "not_needed"
            if obs.input_mode != 1 or obs.menu or obs.prompt:
                self._event("save_skipped", {"reason": "Unresolved interface; disconnect without guessing keys"})
                return "disconnect"
            saved = await self._bounded(self.adapter.dispatch(Action("save", reason="Graceful shutdown")))
            if saved.input_mode == 8 and any(word in saved.prompt.lower() for word in ("save", "saving")):
                await self._bounded(self.adapter.dispatch(Action("confirm", "y", "Confirm saving the game")))
            elif saved.input_mode == 8:
                self._event("save_skipped", {"reason": "Unrecognised save confirmation", "prompt": saved.prompt})
                return "disconnect"
            self._event("saved", {})
            return "saved"
        except Exception as exc:
            # A save may already have reached the server. Never replay it.
            self._event("save_error", {"error": str(exc), "type": type(exc).__name__})
            return "uncertain"

    async def _drain_terminal(self) -> None:
        for _ in range(8):
            obs = self._snapshot()
            if obs.input_mode != 5 or obs.menu.get("type") == "newgame-choice":
                return
            try:
                await self._bounded(self.adapter.key(" "))
                self._event("terminal_acknowledgement", {"kind": "more"})
            except Exception as exc:
                self._event("terminal_drain_error", {"error": str(exc)})
                return
        self._event("terminal_drain_limit", {"acknowledgements": 8})

    async def _close_control(self) -> None:
        if self._server is not None:
            self._server.close()
            await asyncio.sleep(0)
        self._complete_step(False)
        clients = list(self._clients)
        if clients:
            _, pending = await asyncio.wait(clients, timeout=min(0.25, self.config.timeout))
            for task in pending:
                task.cancel()
            await asyncio.gather(*clients, return_exceptions=True)
        if self._server is not None:
            try:
                await self._bounded(self._server.wait_closed())
            except Exception as exc:
                self._event("control_cleanup_error", {"error": str(exc)})
        for path, identity in reversed(list(self._owned_paths.items())):
            try:
                stat = path.lstat()
                if (stat.st_dev, stat.st_ino) == identity:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._event("control_cleanup_error", {"path": str(path), "error": str(exc)})
        self._owned_paths.clear()

    async def run(self, *, stop_on_pause: bool = False) -> dict:
        result = "stopped"
        details: dict = {}
        try:
            await self._open_control()
            self._publish()
            async with self._boundary:
                try:
                    self.observation = await self._bounded(self.adapter.connect())
                    self._connected = True
                except _TRANSPORT_ERRORS as exc:
                    await self._reconnect(exc)
                if not self._stopping and not (self.observation.dead or self.observation.game_over):
                    self.observation = await self._bounded(self.adapter.create_character())
                self._ready = bool(self.observation.hp_max)
                self._initial_turn = self.observation.turn
                if self._ready:
                    self._event("character_ready", {"observation": self.observation.to_dict()})
                if self._ready and not self._stopping and not (self.observation.dead or self.observation.game_over):
                    await self._start_video()
                self._publish()
                if self._ready:
                    print("BOT_READY " + json.dumps(self.status()), flush=True)
            while True:
                if self.observation.dead or self.observation.game_over:
                    result = "dead" if self.observation.dead else "game_over"
                    details["end_reason"] = getattr(self.adapter.state, "end_reason", "")
                    details["messages"] = list(self.observation.messages)
                    await self._drain_terminal()
                    details["end_reason"] = details["end_reason"] or getattr(self.adapter.state, "end_reason", "")
                    self.reason = details["end_reason"] or result
                    break
                if self._stopping:
                    break
                if stop_on_pause and self._paused:
                    result = "paused"
                    details["pause_reason"] = self.reason or "Execution paused"
                    break
                if self.actions >= self.config.max_actions:
                    result = "action_limit"
                    break
                if self.observation.turn - self._initial_turn >= self.config.max_turns:
                    result = "turn_limit"
                    break
                remaining = self._next_action - asyncio.get_running_loop().time()
                if (self._paused and not self._steps) or (remaining > 0 and not self._steps):
                    await self._idle(min(0.25, self.config.timeout,
                                         remaining if remaining > 0 and not self._paused else 0.25))
                    continue
                async with self._boundary:
                    if self._stopping or (self._paused and not self._steps):
                        continue
                    await self._act()
                # Strategies and fakes may complete synchronously; yield to controls.
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            self.request_stop()
            details["cancelled"] = True
        except Exception as exc:
            result = "error"
            details["error"] = str(exc)
            details["error_type"] = type(exc).__name__
            self.reason = f"{type(exc).__name__}: {exc}"
            self._event("supervisor_error", {"error": str(exc), "type": type(exc).__name__})
        finally:
            self._stopping = True
            async with self._boundary:
                await self._set_video_paused(True)
                self._complete_step(False)
            try:
                details["save"] = await self._save()
                if result not in {"dead", "game_over"} and (self.observation.dead or self.observation.game_over):
                    result = "dead" if self.observation.dead else "game_over"
                    details["end_reason"] = getattr(self.adapter.state, "end_reason", "")
                    details["messages"] = list(self.observation.messages)
                    await self._drain_terminal()
                    details["end_reason"] = details["end_reason"] or getattr(self.adapter.state, "end_reason", "")
                    self.reason = details["end_reason"] or result
            finally:
                try:
                    await self._bounded(self.adapter.close())
                except Exception as exc:
                    self._event("close_error", {"error": str(exc)})
                async with self._boundary:
                    await self._close_video()
                if self._video_path is not None:
                    details["video"] = str(self._video_path)
                await self._close_control()
                self._finished = True
                self.state = (result if result in {"dead", "game_over", "error", "stopped", "paused"} else "finished")
                details.update(state=self.state, actions=self.actions, initial_turn=self._initial_turn,
                               turns_played=max(0, self.observation.turn - self._initial_turn))
                try:
                    self._publish(live=True)
                    summary = self.recorder.finish(result, self.observation, details)
                finally:
                    self.recorder.close()
        return {**summary, "state": self.state, "run_dir": str(self.recorder.path)}
