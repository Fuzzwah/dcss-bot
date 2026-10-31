"""Flushed public-state run records and optional anonymous native WebTiles video.

Playwright captures active segments; system FFmpeg joins them without re-encoding.
Message-derived kill totals cover this run, not historical messages in a resumed save.
Identical saturated message tails cannot reveal otherwise unobservable new messages.
"""
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from .danger import assess_danger
from .config import DCSS_COMMIT, DCSS_VERSION
from .models import Action, Observation


_LOG = logging.getLogger(__name__)
_SECRET_KEY = re.compile(r"password|passwd|secret|token|authorization|cookie|api[_-]?key", re.I)
_URL = re.compile(r"(?:https?|wss?)://[^\s<>\"']+")
_KILL = re.compile(r"\bYou (?:kill|slay|destroy|blow up)\b|You feel a bit more experienced\.", re.I)
_DEATH_CAUSE = re.compile(r"slain by|killed by|mangled by|demolished by|annihilated by|succumbed to|burnt to a crisp|drowned|starved to death", re.I)
_DAMAGE = re.compile(r"\b(?:hits?|bites?|claws?|stings?|mauls?|burns?|poison(?:ed)?|sick|damage|engulfs?|crush(?:es)?|constricts?|smite[sd]?|smitten|smote|pain shoots through)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_messages(previous: tuple[str, ...], current: tuple[str, ...]) -> tuple[str, ...]:
    # Sequence overlap preserves repeated identical kills separated by other messages;
    # a set would incorrectly merge distinct monsters of the same kind.
    for size in range(min(len(previous), len(current)), 0, -1):
        if previous[-size:] == current[:size]:
            return current[size:]
    return current


class Recorder:
    def __init__(self, root: Path, metadata: dict) -> None:
        self.started_at = _now()
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex[:12]
        self.path = Path(root) / self.run_id
        self.path.mkdir(parents=True, exist_ok=False)
        self._secrets: set[str] = set()
        self._collect_secrets(metadata)
        self._streams = {}
        self._closed = False
        self._summary = None
        self._event_sequence = 0
        self._decisions = 0
        self._start_turn = None
        self._previous_decision_hp = None
        self._messages: tuple[str, ...] = ()
        self._deepest_depth = 0
        self._deepest_location = ""
        self._max_xl = 1
        self._kills = 0
        self._consumables: Counter = Counter()
        self._actions: Counter = Counter()
        self._retreats = 0
        self._emergencies = 0
        self._elapsed = 0.0
        data = dict(metadata)
        data.setdefault("config", dict(metadata))
        data.update(run_id=self.run_id, started_at=self.started_at,
                    engine={"name": "DCSS", "version": DCSS_VERSION, "commit": DCSS_COMMIT})
        try:
            self._atomic_json("metadata.json", data)
            for name in ("actions", "observations", "events"):
                self._streams[name] = (self.path / f"{name}.jsonl").open("a", encoding="utf-8")
            self.event("run_started", {"run_id": self.run_id})
        except BaseException:
            self.close()
            raise

    def _collect_secrets(self, value) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if _SECRET_KEY.search(str(key)) and isinstance(item, str) and item:
                    self._secrets.add(item)
                else:
                    self._collect_secrets(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self._collect_secrets(item)
        elif isinstance(value, str):
            for match in _URL.finditer(value):
                try:
                    parts = urlsplit(match.group())
                    if parts.password:
                        self._secrets.add(unquote(parts.password))
                    for key, item in parse_qsl(parts.query):
                        if _SECRET_KEY.search(key) and item:
                            self._secrets.add(item)
                except ValueError:
                    pass

    @staticmethod
    def _safe_url(match) -> str:
        try:
            parts = urlsplit(match.group())
            netloc = parts.netloc.rsplit("@", 1)[-1]
            query = urlencode([(key, "[REDACTED]" if _SECRET_KEY.search(key) else value)
                               for key, value in parse_qsl(parts.query, keep_blank_values=True)])
            return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
        except ValueError:
            return "[REDACTED URL]"

    def _public(self, value):
        if isinstance(value, dict):
            return {str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else self._public(item)
                    for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._public(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, str):
            value = _URL.sub(self._safe_url, value)
            value = re.sub(r"(?i)\b(password|passwd|token|api[_-]?key|authorization)\s*[=:]\s*[^\s,;]+",
                           r"\1=[REDACTED]", value)
            for secret in sorted(self._secrets, key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
        return value

    def _atomic_json(self, filename: str, data: dict) -> None:
        temporary = self.path / f".{filename}.{uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(self._public(data), stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path / filename)
        finally:
            temporary.unlink(missing_ok=True)

    def _append(self, stream: str, data: dict) -> None:
        if self._closed:
            raise RuntimeError("Recorder is closed")
        self._collect_secrets(data)
        handle = self._streams[stream]
        handle.write(json.dumps(self._public(data), ensure_ascii=False, allow_nan=False) + "\n")
        handle.flush()

    def event(self, kind: str, data: dict) -> None:
        self._event_sequence += 1
        self._append("events", {"sequence": self._event_sequence, "timestamp": _now(),
                                "kind": kind, "data": data})

    def _observe(self, observation: Observation) -> tuple[str, ...]:
        if self._start_turn is None:
            self._start_turn = observation.turn
            new = ()
        else:
            new = _new_messages(self._messages, observation.messages)
        self._kills += sum(bool(_KILL.search(message)) for message in new)
        self._messages = observation.messages
        if observation.depth > self._deepest_depth:
            self._deepest_depth = observation.depth
            self._deepest_location = observation.location
        self._max_xl = max(self._max_xl, observation.xl)
        return new

    @staticmethod
    def _consumed(before: Observation, action: Action, after: Observation) -> dict:
        remaining = {item.slot: item for item in after.inventory}
        used: Counter = Counter()
        for item in before.inventory:
            selected = action.kind in {"quaff", "read"} and item.letter == action.value
            if not selected and not re.search(r"\b(?:potions?|scrolls?)\b", item.name, re.I):
                continue
            other = remaining.get(item.slot)
            quantity = max(0, item.quantity - (other.quantity if other else 0))
            if quantity:
                name = other.name if other else item.name
                name = re.sub(r"^(?:\d+|an?|the)\s+", "", name, flags=re.I)
                name = re.sub(r"\b(potion|scroll)s\b", r"\1", name, flags=re.I)
                used[name] += quantity
        return dict(used)

    def record(self, before: Observation, action: Action, after: Observation, elapsed: float) -> None:
        if self._summary is not None:
            raise RuntimeError("Recorder is finished")
        previous_hp = self._previous_decision_hp
        self._previous_decision_hp = before.hp
        self._observe(before)
        messages = self._observe(after)
        danger = assess_danger(before, previous_hp)
        emergency = danger.emergency or "emergency" in action.objective.lower()
        consumed = self._consumed(before, action, after)
        self._decisions += 1
        self._actions[action.kind] += 1
        self._consumables.update(consumed)
        self._retreats += action.objective.lower() == "retreat"
        self._emergencies += emergency
        self._elapsed += elapsed
        timestamp = _now()
        outcome = {"turn_delta": after.turn - before.turn, "hp_delta": after.hp - before.hp,
                   "position_changed": before.position != after.position,
                   "location_changed": before.location != after.location,
                   "consumables_used": consumed, "new_messages": messages,
                   "dead": after.dead, "game_over": after.game_over,
                   "state_changed": before != after}
        if action.assessment:
            before_adjacent = sum(m.distance(before.position) <= 1 for m in before.hostiles)
            after_adjacent = sum(m.distance(after.position) <= 1 for m in after.hostiles)
            outcome["tactical_change"] = {
                "visible_hostiles_delta": len(after.hostiles) - len(before.hostiles),
                "adjacent_attackers_delta": after_adjacent - before_adjacent,
                "hp_fraction_delta": after.hp_ratio - before.hp_ratio,
                "interpretation": "Observed changes only; LOS loss is not proof of an escaped or dead enemy",
            }
        self._append("observations", {"sequence": self._decisions, "timestamp": timestamp,
                                      "phase": "before", "observation": before.to_dict()})
        self._append("actions", {"sequence": self._decisions, "timestamp": timestamp,
                                 "action": action.to_dict(), "before": before.to_dict(),
                                 "after": after.to_dict(), "elapsed": elapsed, "outcome": outcome,
                                 "emergency": emergency, "danger_reasons": danger.reasons})
        self._append("observations", {"sequence": self._decisions, "timestamp": timestamp,
                                      "phase": "after", "observation": after.to_dict()})
        self.event("decision", {"sequence": self._decisions, "action": action.to_dict(), "outcome": outcome})

    def publish_status(self, status: dict) -> None:
        self._collect_secrets(status)
        self._atomic_json("status.json", {**status, "run_id": self.run_id, "updated_at": _now()})

    def finish(self, result: str, observation: Observation, details: dict | None = None) -> dict:
        if self._summary is not None:
            return self._summary
        self._observe(observation)
        cause = None
        cause_source = None
        if observation.dead:
            cause = next((m for m in reversed(observation.messages) if _DEATH_CAUSE.search(m)), None)
            cause_source = "explicit_message" if cause else None
            if not cause:
                cause = next((m for m in reversed(observation.messages) if _DAMAGE.search(m)), None)
                cause_source = "last_damage_message" if cause else None
        if details and details.get("death_cause"):
            cause, cause_source = details["death_cause"], "details"
        summary = {"run_id": self.run_id, "started_at": self.started_at, "finished_at": _now(),
                   "result": result, "start_turn": self._start_turn,
                   "turns_survived": max(0, observation.turn - self._start_turn),
                   "total_game_turn": observation.turn, "deepest_depth": self._deepest_depth,
                   "deepest_location": self._deepest_location, "max_xl": self._max_xl,
                   "kills": self._kills, "consumables_used": dict(self._consumables),
                   "consumables_total": sum(self._consumables.values()),
                   "retreats": self._retreats, "emergencies": self._emergencies,
                   "decisions": self._decisions, "action_counts": dict(self._actions),
                   "decision_seconds": self._elapsed, "dead": observation.dead,
                   "game_over": observation.game_over, "death_cause": cause,
                   "death_cause_source": cause_source, "recent_messages": observation.messages[-20:],
                   "final_observation": observation.to_dict(), "details": details or {}}
        self._collect_secrets(summary)
        summary = self._public(summary)
        self._atomic_json("summary.json", summary)
        self.event("run_finished", {"result": result, "turns_survived": summary["turns_survived"]})
        self.publish_status({"state": "finished", "summary": summary})
        self._summary = summary
        return summary

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for stream in self._streams.values():
            stream.close()


class VideoRecorder:
    """Anonymous native WebTiles capture, with a finalized context per active interval.

    Callers serialize transitions at gameplay boundaries. Failures propagate after
    bounded resource cleanup; finalized segments remain available for recovery.
    """
    _START_TIMEOUT = 40
    _SEGMENT_TIMEOUT = 15
    _RESOURCE_TIMEOUT = 5
    _JOIN_TIMEOUT = 30

    def __init__(self, spectator_url: str, path: Path) -> None:
        self.spectator_url = spectator_url
        self.path = Path(path)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._video = None
        self._output = None
        self._started = False
        self._closed = False
        self._failed = False
        self._close_task = None
        self._segment_dir = None
        self._segments: list[Path] = []
        self._ffmpeg = None

    async def start(self, *, paused: bool = False) -> None:
        if self._closed or self._started:
            raise RuntimeError("VideoRecorder cannot be started twice")
        parts = urlsplit(self.spectator_url)
        if parts.scheme not in {"http", "https"} or parts.username or parts.password:
            raise ValueError("Video requires an anonymous HTTP(S) spectator URL")
        if not parts.fragment.startswith("watch-") or not parts.fragment[6:]:
            raise ValueError("Video requires a #watch-character spectator URL")
        self._started = True
        if not paused:
            await self.resume()

    async def resume(self) -> None:
        if not self._started or self._closed or self._failed:
            raise RuntimeError("VideoRecorder is not available for recording")
        if self._context is not None:
            return
        try:
            async with asyncio.timeout(self._START_TIMEOUT):
                if self._browser is None:
                    self._ffmpeg = shutil.which("ffmpeg")
                    if self._ffmpeg is None:
                        raise RuntimeError("Video recording requires system FFmpeg on PATH")
                    from playwright.async_api import async_playwright
                    self.path.mkdir(parents=True, exist_ok=True)
                    self._segment_dir = self.path.resolve() / (".video-" + uuid4().hex)
                    self._segment_dir.mkdir()
                    self._playwright = await async_playwright().start()
                    self._browser = await self._playwright.chromium.launch(headless=True)
                self._context = await self._browser.new_context(
                    viewport={"width": 1280, "height": 960},
                    record_video_dir=str(self._segment_dir),
                    record_video_size={"width": 1280, "height": 960})
                self._page = await self._context.new_page()
                self._video = self._page.video
                await self._page.goto(self.spectator_url, wait_until="domcontentloaded", timeout=20000)
                await self._page.locator("canvas#dungeon").wait_for(state="visible", timeout=20000)
                await self._page.wait_for_function("""name => {
                    const canvas = document.querySelector('canvas#dungeon');
                    const title = document.querySelector('#stats_titleline');
                    const hp = document.querySelector('#stats_hp_max');
                    return canvas && canvas.width > 0 && canvas.height > 0 && title && hp &&
                        title.innerText.includes(name) && Number(hp.innerText) > 0;
                }""", arg=unquote(urlsplit(self.spectator_url).fragment[6:]), timeout=20000)
                await self._page.screenshot(path=str(self.path / "spectator.png"))
        except BaseException as error:
            self._failed = True
            await self._release(cancelled=isinstance(error, asyncio.CancelledError))
            raise

    async def pause(self) -> None:
        if self._context is None:
            return
        try:
            async with asyncio.timeout(self._SEGMENT_TIMEOUT):
                # Let the independent spectator receive the last action, paint it,
                # and deliver a frame to Playwright's 25 fps encoder. Closing at
                # the action acknowledgement can otherwise lose a single step.
                await asyncio.sleep(0.1)
                await self._page.evaluate("""() => new Promise(resolve =>
                    requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
                await asyncio.sleep(0.1)
                await self._context.close()
                source = await self._video.path()
                segment = self._segment_dir / f"segment-{len(self._segments):06d}.webm"
                Path(source).replace(segment)
                self._segments.append(segment)
                self._context = self._page = self._video = None
        except BaseException as error:
            self._failed = True
            await self._release(cancelled=isinstance(error, asyncio.CancelledError))
            raise

    async def _release(self, *, cancelled: bool = False) -> None:
        # A supervisor timeout must not add another full shutdown deadline.
        # Try flushing the context first, then release Chromium and Playwright.
        task = asyncio.create_task(self._release_resources(cancelled=cancelled))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.shield(task)
            raise

    async def _release_resources(self, *, cancelled: bool) -> None:
        resources = ((self._context, "close", self._SEGMENT_TIMEOUT),
                     (self._browser, "close", self._RESOURCE_TIMEOUT),
                     (self._playwright, "stop", self._RESOURCE_TIMEOUT))
        self._context = self._page = self._video = None
        self._browser = self._playwright = None
        for resource, method, timeout in resources:
            if resource is None:
                continue
            if cancelled:
                timeout = min(timeout, 0.3)
            try:
                async with asyncio.timeout(timeout):
                    await getattr(resource, method)()
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                _LOG.warning("Could not release spectator capture resource", exc_info=True)

    async def close(self) -> Path | None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._finalize())
        try:
            return await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            self._close_task.cancel()
            try:
                await asyncio.shield(self._close_task)
            except (asyncio.CancelledError, Exception):
                pass
            await self._release(cancelled=True)
            raise

    async def _finalize(self) -> Path | None:
        try:
            await self.pause()
        finally:
            await self._release()
        if not self._segments:
            return None
        destination = self.path / "gameplay.webm"
        if len(self._segments) == 1:
            # Retain evidence after an earlier capture failure, even when a
            # usable prefix can still be published.
            if self._failed:
                shutil.copyfile(self._segments[0], destination)
            else:
                self._segments[0].replace(destination)
        else:
            await self._join(destination)
        self._output = destination
        if not self._failed:
            shutil.rmtree(self._segment_dir)
        return self._output

    async def _join(self, destination: Path) -> None:
        # All manifest names are generated ASCII basenames. Neither the run
        # directory nor the spectator URL is interpolated into FFmpeg syntax.
        manifest = self._segment_dir / "segments.txt"
        manifest.write_text("".join(f"file '{segment.name}'\n" for segment in self._segments),
                            encoding="utf-8")
        process = None
        joined = self._segment_dir / "joined.webm"
        try:
            async with asyncio.timeout(self._JOIN_TIMEOUT):
                process = await asyncio.create_subprocess_exec(
                    self._ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "1", "-i", manifest.name,
                    "-c", "copy", "-y", joined.name, cwd=self._segment_dir,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                _, stderr = await process.communicate()
                if process.returncode:
                    raise RuntimeError("Could not join spectator video with FFmpeg: " +
                                       stderr.decode("utf-8", errors="replace").strip())
                joined.replace(destination)
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                async with asyncio.timeout(0.3):
                    await process.wait()
