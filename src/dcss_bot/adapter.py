"""Single-flight WebTiles adapter; no strategy and no hidden state queries."""
import asyncio
import hashlib
import json
import re
import zlib
from collections.abc import Callable
from dataclasses import replace
from html.parser import HTMLParser

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .characters.base import CharacterProfile
from .config import validate_seed
from .item_names import category_from_name, public_item_name, scroll_named
from .models import Action, Observation
from .state import GameState, plain
from .terrain import DOWNSTAIRS, UPSTAIRS

_NATIVE_INTERRUPT_KEY = 18  # Ctrl-R: CMD_REDRAW_SCREEN, not Ctrl-L (attack right).
_NATIVE_MAP_UI = 2  # tileweb.h: UI_VIEW_MAP
_NATIVE_MAP_CURSOR = 2  # cursor-type.h: CURSOR_MAP


class ProtocolError(RuntimeError):
    pass


class _SaveLinks(HTMLParser):
    """Read only the saved-character labels exposed in the upstream lobby."""

    def __init__(self, game_id: str) -> None:
        super().__init__()
        self.href = "#play-" + game_id
        self.current: list[str] | None = None
        self.labels: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "a" and dict(attrs).get("href") == self.href:
            self.current = []

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current is not None:
            self.labels.append("".join(self.current).strip())
            self.current = None


class WebTilesAdapter:
    def __init__(self, url: str, username: str, password: str, game_id: str = "dcss",
                 timeout: float = 15.0, event: Callable | None = None, *,
                 character: CharacterProfile, seed: int | None = None) -> None:
        validate_seed(seed)
        self.url, self.username, self.password = url, username, password
        self.game_id, self.timeout = game_id, timeout
        self.character = character
        self.event = event or (lambda *args, **kwargs: None)
        self.state = GameState()
        self.socket = None
        self.inflater = zlib.decompressobj(-zlib.MAX_WBITS)
        self._lock = asyncio.Lock()
        self.seed = seed
        self.seed_rc: str | None = None
        self.seed_rc_sha256: str | None = None
        self.engine_version: str | None = None
        # Confirmation is an exact RC readback, not an engine-reported seed.
        self.seed_confirmed = False
        self._play_sent = False
        self._seed_finished = False
        self._expect_fresh_character = False
        self._save_confirmation_revision: int | None = None
        self.native_activity = ""
        self._native_travel_target: tuple[str, tuple[int, int]] | None = None
        self._map_pending = False
        self._map_opened = False
        self._map_ui_state: int | None = None
        self._map_cursor: tuple[int, int] | None = None
        self._map_cursor_revision = 0
        self._automation_guard: Callable[[Observation], str] | None = None

    async def send(self, message: dict) -> None:
        if self.socket is None:
            raise ProtocolError("Not connected")
        await self.socket.send(json.dumps(message))

    def decode(self, frame: str | bytes) -> list[dict]:
        try:
            if isinstance(frame, bytes):
                frame = self.inflater.decompress(frame + b"\x00\x00\xff\xff").decode()
            obj = json.loads(frame)
        except (ValueError, UnicodeError, zlib.error) as exc:
            raise ProtocolError("Invalid WebTiles JSON") from exc
        messages = obj.get("msgs", [obj]) if isinstance(obj, dict) else obj
        if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
            raise ProtocolError("Invalid WebTiles message batch")
        return messages

    async def receive(self, timeout: float | None = None) -> list[dict]:
        """Wait for a response, then coalesce one render's incremental packets.

        The server does not assign request IDs. Never pipeline game actions or
        blindly replay a command after transport failure.
        """
        messages = []
        deadline = asyncio.get_running_loop().time() + (timeout or self.timeout)
        first = True
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                if messages:
                    break
                raise TimeoutError("No WebTiles response")
            try:
                frame = await asyncio.wait_for(self.socket.recv(), remaining if first else min(0.12, remaining))
            except asyncio.TimeoutError:
                if first:
                    raise TimeoutError("No WebTiles response")
                break
            batch = self.decode(frame)
            for message in batch:
                if message.get("msg") == "ping":
                    await self.send({"msg": "pong"})
                    continue
                self.state.apply(message)
                if message.get("msg") == "ui_state":
                    self._map_ui_state = message.get("state")
                    if self._map_pending and self._map_ui_state == _NATIVE_MAP_UI:
                        self._map_opened = True
                elif message.get("msg") == "cursor" and message.get("id") == _NATIVE_MAP_CURSOR:
                    loc = message.get("loc", {})
                    self._map_cursor = ((loc["x"], loc["y"]) if (
                        type(loc.get("x")) is int and type(loc.get("y")) is int) else None)
                    self._map_cursor_revision += 1
                if message.get("msg") == "version" and isinstance(message.get("text"), str):
                    self.engine_version = message["text"]
                    self.event("engine_version", {"text": message["text"]})
                if self.seed is not None and (self.state.dead or self.state.game_over):
                    self._seed_finished = True
                messages.append(message)
                if message.get("msg") == "set_game_links":
                    self._check_saved_character(message)
            if messages:
                first = False
        return messages

    async def _protocol_response(self, kinds: set[str], messages: list[dict] | None = None) -> list[dict]:
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            messages = messages or []
            if any(m.get("msg") in {"auth_error", "close", "login_required", "register_fail"}
                   for m in messages):
                raise ProtocolError("WebTiles rejected protocol setup")
            if any(m.get("msg") in kinds for m in messages):
                return messages
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("No WebTiles " + "/".join(sorted(kinds)) + " response")
            messages = await self.receive(remaining)

    async def _read_rc(self) -> str:
        await self.send({"msg": "get_rc", "game_id": self.game_id})
        messages = await self._protocol_response({"rcfile_contents"})
        replies = [m for m in messages if m.get("msg") == "rcfile_contents"]
        if (len(replies) != 1 or replies[0].get("game_id") != self.game_id
                or not isinstance(replies[0].get("contents"), str)):
            raise ProtocolError("Invalid WebTiles rcfile_contents response")
        return replies[0]["contents"]

    async def _configure_rc(self) -> None:
        original = await self._read_rc()
        settings = self.character.rc_settings()
        replaced = {"species", "background", "weapon", *(key.casefold() for key in settings)}
        if self.seed is not None:
            replaced.update({"game_seed", "pregen_dungeon"})
        # Remove all old assignments, including additive/reset forms, while
        # retaining unrelated comments, options and line endings byte-for-byte.
        retained = []
        for line in original.splitlines(keepends=True):
            assignment = re.match(r"^\s*([\w.]+)\s*[+\-^:]?=", line)
            if assignment is None or assignment[1].casefold() not in replaced:
                retained.append(line)
        contents = "".join(retained)
        if contents and not contents.endswith(("\n", "\r")):
            contents += "\n"
        contents += "".join(f"{key} = {value}\n" for key, value in settings.items())
        if self.seed is not None:
            # seed_from_rc retains its first nonzero value. Incremental
            # generation is deterministic and supported in DGAMELAUNCH builds.
            contents = f"game_seed = {self.seed}\n" + contents + "pregen_dungeon = incremental\n"
        await self.send({"msg": "set_rc", "game_id": self.game_id, "contents": contents})
        # set_rc has no acknowledgement; ordering on the socket makes this
        # subsequent get_rc a readback of the attempted write.
        if await self._read_rc() != contents:
            raise ProtocolError("Character/seed RC readback did not match; refusing to play")
        if self.seed is not None:
            self.seed_rc = contents
            self.seed_rc_sha256 = hashlib.sha256(contents.encode()).hexdigest()
            self.seed_confirmed = True
            self.event("seed_configured", {
                "requested_seed": self.seed, "confirmed_seed": self.seed,
                "confirmation": "rc_readback", "rc_sha256": self.seed_rc_sha256,
                "contents": contents, "pregen_dungeon": "incremental",
            })

    def _check_saved_character(self, message: dict) -> None:
        if not isinstance(message.get("content"), str):
            raise ProtocolError("Invalid saved-character lobby response")
        parser = _SaveLinks(self.game_id)
        parser.feed(message["content"])
        for label in parser.labels:
            # Upstream short_desc(false): "[Name, a level N Species Background of God]".
            # "[playing]" has no background evidence; verify public player state on attach.
            match = re.fullmatch(r"\[[^,]+, a level \d+ (.+)\]", label)
            if match and not self.character.matches_saved_description(match[1]):
                raise ProtocolError(
                    f"Saved character {match[1]} does not match selected {self.character.identifier}")

    def _verify_character(self, observation: Observation) -> None:
        if not self.character.matches_observation(observation):
            raise ProtocolError(
                f"Public character identity does not match selected {self.character.identifier}: "
                f"species={observation.species!r}, god={observation.god!r}")
        # A fresh native greeting exposes the original background; resumed
        # player packets do not. Never infer a background from hidden save data.
        has_welcome = any(message.startswith("Welcome, ") for message in observation.messages)
        if (self._expect_fresh_character or has_welcome) and not self.character.matches_start(observation):
            raise ProtocolError(f"Fresh character does not match selected {self.character.identifier}")
        self._expect_fresh_character = False

    async def _require_saved_character(self, messages: list[dict]) -> None:
        # Upstream has no resume-only play command. Fail closed unless the
        # authenticated lobby advertises a save or an already-running game.
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            parser = _SaveLinks(self.game_id)
            for message in messages:
                if message.get("msg") in {"auth_error", "close", "login_required"}:
                    raise ProtocolError("WebTiles rejected seeded resume")
                if message.get("msg") == "set_game_links":
                    if not isinstance(message.get("content"), str):
                        raise ProtocolError("Invalid saved-character lobby response")
                    parser.feed(message["content"])
            if any(label.startswith("[") and label.endswith("]") and label != "[slot full]"
                   for label in parser.labels):
                return
            # Upstream first renders empty links, then asynchronously obtains
            # save info. An empty first response does not prove there is no save.
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                messages = await self.receive(remaining)
            except TimeoutError:
                break
        raise ProtocolError("No resumable seeded character advertised; refusing to create another")

    async def connect(self) -> Observation:
        if self.seed is not None and (self._seed_finished or self.state.dead or self.state.game_over):
            raise ProtocolError("Cannot reconnect a finished seeded run")
        await self.close()
        self.state = GameState()
        self._map_ui_state = None
        self._map_opened = False
        self._map_cursor = None
        self.inflater = zlib.decompressobj(-zlib.MAX_WBITS)
        self._expect_fresh_character = False
        self.socket = await connect(self.url, subprotocols=["no-compression"],
                                    compression=None, max_size=16 * 1024 * 1024,
                                    open_timeout=self.timeout, ping_interval=20)
        try:
            await self.receive()
            await self.send({"msg": "login", "username": self.username, "password": self.password})
            messages = await self._protocol_response({"login_success", "login_fail"})
            logged_in = any(m.get("msg") == "login_success" for m in messages)
            if self.seed is not None and not self._play_sent and logged_in:
                raise ProtocolError("Seeded runs require a newly registered account; existing account untouched")
            if not logged_in:
                if self.seed is not None and self._play_sent:
                    raise ProtocolError("Cannot register another account while resuming a seeded run")
                await self.send({"msg": "register", "username": self.username,
                                 "password": self.password, "email": ""})
                messages = await self._protocol_response({"login_success"})
                self._expect_fresh_character = True
            self.event("authenticated", {"username": self.username})
            if self.seed is not None and self._play_sent:
                await self._require_saved_character(messages)
            elif not self._play_sent:
                await self._configure_rc()
            # Mark before sending: an interrupted send must never be retried
            # as fresh seeded creation, even if the server's reply was lost.
            self._play_sent = True
            await self.send({"msg": "play", "game_id": self.game_id})
            await self.receive()
            observation = self.state.observation()
            if observation.hp_max and observation.input_mode != 0 and not (observation.dead or observation.game_over):
                self._verify_character(observation)
            return observation
        except BaseException:
            await self.close()
            raise

    async def reconnect(self, attempts: int = 3) -> Observation:
        for attempt in range(attempts):
            try:
                if attempt:
                    await asyncio.sleep(min(2 ** attempt, 8))
                await self.connect()
                observation = await self._resumed_observation()
                self.event("reconnected", {"attempt": attempt + 1})
                return observation
            except (OSError, ConnectionClosed, TimeoutError) as exc:
                self.event("reconnect_error", {"attempt": attempt + 1, "error": str(exc)})
                if attempt + 1 == attempts:
                    raise
        raise ProtocolError("Reconnect attempts exhausted")

    async def _resumed_observation(self) -> Observation:
        """Wait past game-client/layout packets without replaying creation keys."""
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            observation = self.state.observation()
            if observation.dead or observation.game_over:
                return observation
            if observation.menu.get("type") in {"newgame-choice", "seed-selection"}:
                raise ProtocolError("Reconnect exposed character creation; refusing to start another game")
            if self.native_activity == "travel_downstairs" and self._map_ui_state == _NATIVE_MAP_UI:
                # The attached public UI proves Enter did not leave the map.
                # Close it; never replay the uncertain selection/commit.
                self._map_pending = True
                self._map_opened = True
                self.native_activity = ""
                self._native_travel_target = None
            if self._map_pending and observation.hp_max:
                if self._map_ui_state is None or (
                        self._map_ui_state != _NATIVE_MAP_UI and observation.input_mode == 0
                        and not observation.menu):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError("No native map boundary after reconnect")
                    await self.receive(remaining)
                    continue
                if (self._map_ui_state is not None and self._map_ui_state != _NATIVE_MAP_UI
                        and observation.input_mode == 1 and not observation.menu):
                    self._map_pending = False
                    continue
                await self._cancel_native_map()
                continue
            if (self.native_activity and observation.hp_max and observation.input_mode == 0
                    and not observation.menu):
                # Reconcile the old activity at a real boundary, never replay it.
                await self._cancel_native()
                continue
            if observation.hp_max and (observation.input_mode != 0 or observation.menu):
                if observation.input_mode == 1 and not observation.menu:
                    self.native_activity = ""
                    self._native_travel_target = None
                self._verify_character(observation)
                return await self.inspect_inventory()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("No saved-character state after reconnect")
            await self.receive(remaining)

    async def key(self, key: str | int) -> Observation:
        return await self._command({"msg": "key", "keycode": ord(key) if isinstance(key, str) else key})

    async def _command(self, message: dict) -> Observation:
        deadline = asyncio.get_running_loop().time() + self.timeout
        try:
            await asyncio.wait_for(self.send(message), self.timeout)
            if self.native_activity:
                return await self._await_native(self.native_activity, self._automation_guard)
        except asyncio.CancelledError:
            if self.native_activity and not (self.state.dead or self.state.game_over):
                if self.state.input_mode not in {0, 1} or self.state.observation().menu:
                    # The prompt answer was already sent. Observe its response
                    # before deciding whether the resumed activity needs a key.
                    await self._await_native(self.native_activity, lambda _: "control")
                else:
                    await self._cancel_native()
            raise
        await self.receive()
        # NORMAL (0) is the engine's busy/render state, not COMMAND (1).
        # Generating a level can exceed the packet-coalescing quiet period.
        # Receive its completion without sending or replaying another key.
        while (self.state.input_mode == 0 and not self.state.more
               and not self.state.menus and not self.state.ui
               and not self.state.dead and not self.state.game_over):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("No native input boundary after key")
            await self.receive(remaining)
        return self.state.observation()

    async def _native_automation(self, kind: str,
                                 guard: Callable[[Observation], str] | None,
                                 delay: float | None) -> Observation:
        """Await native progress or acknowledge a safe cancellation boundary.

        Pinned travel.cc:1062 and delay.cc:407 only poll kbhit(), leaving
        the interrupt queued. Ctrl-R is a harmless redraw at COMMAND too;
        Escape is reserved for actual menus/prompts.
        """
        supported = await self._native_options(kind, delay)
        if not supported:
            return replace(self.state.observation(), native_automation=kind,
                           native_result="unsupported_delay")
        reason = guard(self.state.observation()) if guard else ""
        if reason:
            return replace(self.state.observation(), native_automation=kind, native_result=reason)
        self.native_activity = kind
        await asyncio.wait_for(self.send(
            {"msg": "key", "keycode": ord("5" if kind == "rest" else "o")}), self.timeout)
        return await self._await_native(kind, guard)

    async def _native_options(self, kind: str, delay: float | None) -> bool:
        if kind in {"explore", "travel_downstairs"} and delay is not None:
            if delay > 2:
                return False
            if not 0 <= delay <= 2:
                raise ProtocolError("Native travel requires a nonnegative finite delay")
        async def send(message: dict) -> None:
            await asyncio.wait_for(self.send(message), self.timeout)
        # These are native public option messages, not a server-side macro.
        # Reassert key cancellation and profile display settings on reconnect.
        await send({"msg": "set_option", "line": "travel_key_stop = true"})
        settings = self.character.rc_settings()
        if kind in {"explore", "travel_downstairs"} and delay is not None:
            settings = {**settings, "travel_delay": str(round(delay * 1000)),
                        "explore_delay": str(round(delay * 1000))}
        for name, value in settings.items():
            if name in {"travel_delay", "explore_delay", "rest_delay", "tile_runrest_rate"}:
                await send({"msg": "set_option", "line": f"{name} = {value}"})
        return True

    @staticmethod
    def _travel_unsafe(observed: Observation) -> bool:
        return bool(observed.hostiles or observed.hp_ratio < 1 or observed.statuses
                    or any((cell.x, cell.y) == observed.position and cell.cloud
                           for cell in observed.cells))

    def _native_travel_arrived(self, observed: Observation) -> bool:
        if self._native_travel_target is None:
            return False
        location, target = self._native_travel_target
        return (observed.input_mode == 1 and not observed.menu
                and observed.location == location and observed.position == target
                and any((cell.x, cell.y) == target and cell.feature in DOWNSTAIRS
                        and not cell.cloud for cell in observed.cells))

    async def _map_key(self, key: str,
                       guard: Callable[[Observation], str] | None) -> Observation:
        """Await this map key's fresh public cursor, not a quiet packet gap.

        tileweb.cc:1863 sends CURSOR_MAP in the same origin-relative coordinates
        as player/map packets. UI_VIEW_MAP is independent of input_mode: the
        map's event loop can remain NORMAL (0).
        """
        revision = self._map_cursor_revision
        await asyncio.wait_for(self.send({"msg": "key", "keycode": ord(key)}), self.timeout)
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            observed = self.state.observation()
            if (observed.dead or observed.game_over or observed.menu
                    or observed.input_mode not in {0, 1}
                    or key != "X" and self._map_ui_state not in {None, _NATIVE_MAP_UI}):
                raise ProtocolError("Native stair travel opened an unexpected interface")
            if self._map_ui_state == _NATIVE_MAP_UI:
                if self._travel_unsafe(observed) or guard and guard(observed):
                    return observed
                if self._map_cursor_revision > revision and self._map_cursor is not None:
                    return observed
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("No fresh native map cursor after selection")
            try:
                await self.receive(min(.25, remaining))
            except TimeoutError:
                continue

    async def _cancel_native_map(self) -> Observation:
        """Close only observed map/menu layers; never queue a gameplay key."""
        deadline = asyncio.get_running_loop().time() + self.timeout

        async def response(previous_menu: dict | None = None) -> Observation:
            while True:
                observed = self.state.observation()
                if observed.dead or observed.game_over:
                    self._map_pending = False
                    return observed
                if (self._map_ui_state is not None and self._map_ui_state != _NATIVE_MAP_UI
                        and observed.input_mode == 1 and not observed.menu):
                    self._map_pending = False
                    return observed
                if previous_menu and observed.menu != previous_menu:
                    return observed
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("No native command boundary after map cancellation")
                await self.receive(remaining)

        for _ in range(3):
            # An interrupted X send is ambiguous. Observe its UI before deciding
            # whether Escape belongs to a map, rather than replaying X.
            while self._map_ui_state is None or (
                    not self._map_opened and not self.state.observation().menu):
                if self.state.dead or self.state.game_over:
                    self._map_pending = False
                    return replace(self.state.observation(), native_automation="travel_downstairs",
                                   native_result="interrupted")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("No native map state after interrupted map command")
                await self.receive(remaining)
            observed = self.state.observation()
            if observed.dead or observed.game_over:
                self._map_pending = False
                return replace(observed, native_automation="travel_downstairs", native_result="interrupted")
            if self._map_ui_state != _NATIVE_MAP_UI and not observed.menu:
                observed = await response()
                return replace(observed, native_automation="travel_downstairs", native_result="control")
            previous_menu = observed.menu
            try:
                await asyncio.wait_for(self.send({"msg": "key", "keycode": 27}), self.timeout)
            except asyncio.CancelledError:
                await response(previous_menu)
                raise
            observed = await response(previous_menu)
            if not self._map_pending:
                return replace(observed, native_automation="travel_downstairs", native_result="control")
        raise ProtocolError("Native map cancellation exceeded three interface layers")

    async def _travel_downstairs(self, value: str,
                                 guard: Callable[[Observation], str] | None,
                                 delay: float | None) -> Observation:
        original = self.state.observation()
        target = tuple(map(int, value.split(",")))
        kind = "travel_downstairs"
        if target == original.position:
            return replace(original, native_automation=kind, native_result="complete")
        if not await self._native_options(kind, delay):
            return replace(original, native_automation=kind, native_result="unsupported_delay")
        reason = guard(self.state.observation()) if guard else ""
        if reason:
            return replace(self.state.observation(), native_automation=kind, native_result=reason)
        self._map_pending = True
        self._map_opened = False
        self._map_ui_state = None
        self._map_cursor = None
        try:
            observed = await self._map_key("X", guard)
            seen: set[tuple[int, int]] = set()
            # Each selected feature is a known map cell. Stop at a repeated
            # cursor or this finite public-map bound, never blindly cycle >.
            for _ in range(len(original.cells) + 1):
                reason = guard(observed) if guard else ""
                if self._travel_unsafe(observed):
                    reason = reason or "interrupted"
                if (observed.location != original.location or observed.position != original.position
                        or observed.turn != original.turn):
                    reason = reason or "interrupted"
                if reason:
                    result = await self._cancel_native_map()
                    return replace(result, native_result=reason)
                observed = await self._map_key(">", guard)
                reason = guard(observed) if guard else ""
                if (self._travel_unsafe(observed) or observed.location != original.location
                        or observed.position != original.position or observed.turn != original.turn):
                    reason = reason or "interrupted"
                if reason:
                    result = await self._cancel_native_map()
                    return replace(result, native_result=reason)
                if self._map_cursor == target:
                    if not any((cell.x, cell.y) == target and cell.feature in DOWNSTAIRS
                               and not cell.cloud for cell in observed.cells):
                        raise ProtocolError("Selected native target is no longer ordinary downstairs")
                    # viewmap.cc:1298 chooses precisely this cursor; main.cc:1853
                    # starts native travel to its same-floor coordinate, not >.
                    self._native_travel_target = (original.location, target)
                    self.native_activity = kind
                    self._map_pending = False
                    await asyncio.wait_for(self.send({"msg": "key", "keycode": 13}), self.timeout)
                    return await self._await_native(kind, guard)
                if self._map_cursor in seen:
                    break
                seen.add(self._map_cursor)
            raise ProtocolError("Requested downstairs absent from native map selection cycle")
        except (ProtocolError, TimeoutError):
            if self._map_pending:
                await self._cancel_native_map()
            raise

    async def _cancel_native(self) -> Observation:
        if self._map_pending:
            return await self._cancel_native_map()
        if self.native_activity == "travel_downstairs" and self._map_ui_state == _NATIVE_MAP_UI:
            # Enter was ambiguously accepted from an already-NORMAL map. Its
            # fresh UI exit must precede a busy-travel Ctrl-R.
            return await self._await_native(self.native_activity, lambda _: "control")
        observed = self.state.observation()
        keycode = (_NATIVE_INTERRUPT_KEY if observed.input_mode in {0, 1}
                   and not observed.menu else 27)
        async def acknowledge() -> Observation:
            if keycode == 27:
                # Closing a page/menu can resume travel. Interrupt that continuation
                # only after its fresh busy response, not by queuing another Escape.
                return await self._await_native(self.native_activity, lambda _: "control")
            return await self._await_native(self.native_activity, None, interrupted="control")
        try:
            await asyncio.wait_for(self.send({"msg": "key", "keycode": keycode}), self.timeout)
            return await acknowledge()
        except asyncio.CancelledError:
            # The interrupt was ambiguously accepted; never replay it.
            await acknowledge()
            raise

    async def _await_native(self, kind: str,
                            guard: Callable[[Observation], str] | None, *,
                            interrupted: str = "") -> Observation:
        """Continue an activity after UI input without replaying its command."""
        loop = asyncio.get_running_loop()
        progress_deadline = loop.time() + self.timeout
        progress = self._native_progress(self.state.observation())
        completion = ""
        boundary_seen = False
        waiting_map_exit = kind == "travel_downstairs" and self._map_ui_state == _NATIVE_MAP_UI
        activity_started = self.state.input_mode == 0 and not interrupted and not waiting_map_exit
        while True:
            observed = self.state.observation()
            if observed.dead or observed.game_over:
                self.native_activity = ""
                self._native_travel_target = None
                return replace(observed, native_automation=kind, native_result="interrupted")
            if (boundary_seen and not waiting_map_exit and (observed.input_mode != 0 or observed.menu)
                    and (activity_started or observed.input_mode != 1 or observed.menu)):
                result = interrupted or completion or (
                    "interrupted" if observed.hostiles or observed.input_mode != 1 or observed.menu
                    else "complete" if kind == "rest" else "interrupted")
                if kind == "travel_downstairs" and not interrupted:
                    result = ("complete" if self._native_travel_arrived(observed)
                              and not self._travel_unsafe(observed) else "interrupted")
                if observed.input_mode == 1 and not observed.menu:
                    self.native_activity = ""
                    self._native_travel_target = None
                return replace(observed, native_automation=kind, native_result=result)
            if not interrupted:
                # After answering a prompt, its old UI remains in state until
                # native responds. Never cancel that already-answered UI again.
                fresh_boundary = not waiting_map_exit and (
                    boundary_seen or observed.input_mode in {0, 1} and not observed.menu)
                reason = guard(observed) if guard and fresh_boundary else ""
                if fresh_boundary and (observed.hostiles
                        or kind in {"explore", "travel_downstairs"} and (
                            observed.hp_ratio < 1 or observed.statuses)
                        or any((cell.x, cell.y) == observed.position and cell.cloud
                               for cell in observed.cells)):
                    reason = reason or "interrupted"
                if kind == "travel_downstairs" and self._native_travel_target:
                    if observed.location != self._native_travel_target[0]:
                        reason = reason or "interrupted"
                if loop.time() >= progress_deadline:
                    if waiting_map_exit:
                        raise TimeoutError("No native map exit after travel commit")
                    reason = reason or "timeout"
                if reason:
                    # kbhit() need not consume this key. A queued redraw is
                    # valid and turn-free even if native completion races it.
                    keycode = (_NATIVE_INTERRUPT_KEY if observed.input_mode in {0, 1}
                               and not observed.menu else 27)
                    await asyncio.wait_for(self.send({"msg": "key", "keycode": keycode}), self.timeout)
                    interrupted = reason
                    if keycode == _NATIVE_INTERRUPT_KEY:
                        # Travel stopping at COMMAND has not consumed the
                        # queued redraw yet. Await that key's own NORMAL/COMMAND.
                        activity_started = False
                        boundary_seen = False
                    progress_deadline = loop.time() + self.timeout
            remaining = progress_deadline - loop.time()
            if remaining <= 0:
                if interrupted:
                    raise TimeoutError("No native input boundary after automation cancellation")
                continue
            try:
                messages = await self.receive(timeout=min(.25, remaining))
            except TimeoutError:
                continue
            fresh_progress = self._native_progress(self.state.observation())
            if fresh_progress != progress and not interrupted:
                progress_deadline = loop.time() + self.timeout
            progress = fresh_progress
            for message in messages:
                if message.get("msg") in {
                        "input_mode", "menu", "update_menu", "update_menu_items",
                        "ui-push", "ui-pop", "ui-state", "close_menu", "close_all_menus"}:
                    boundary_seen = True
                if (waiting_map_exit and message.get("msg") == "ui_state"
                        and message.get("state") != _NATIVE_MAP_UI):
                    # Map input is already NORMAL. Its fresh UI exit, rather
                    # than a repeated 0, acknowledges Enter leaving that loop.
                    waiting_map_exit = False
                    activity_started = True
                    boundary_seen = True
                if message.get("msg") == "input_mode" and message.get("mode") == 0:
                    # A queued redraw/snapshot may repeat old COMMAND before
                    # native has read our key. NORMAL acknowledges that read.
                    activity_started = True
                if message.get("msg") == "msgs":
                    for entry in message.get("messages", []):
                        text = plain(entry.get("text", "")).strip()
                        if kind == "explore" and text == "Done exploring.":
                            completion = "complete"
                        elif kind == "explore" and text.startswith("Partly explored,"):
                            completion = "partial"

    @staticmethod
    def _native_progress(observed: Observation) -> tuple:
        # Pings, redraws, message repetition and mode-0 packets are not game
        # progress and must not keep a stalled native activity alive forever.
        return (observed.turn, observed.time, observed.location, observed.position,
                observed.hp, observed.hp_max, observed.statuses, observed.inventory,
                observed.cells, observed.monsters)

    async def _pickup_select(self, keys: str) -> Observation:
        """Commit one explicitly selected, bounded set from a native pickup menu."""
        observation = self.state.observation()
        title = plain(self._title(observation)).strip()
        if (observation.menu.get("tag") != "pickup" or observation.hostiles
                or not (title.startswith("Pick up what?")
                        or title == "Select pick up quantity by entering a number, then select the item")):
            raise ProtocolError("Pickup selection requires a safe native pickup menu")
        rows = observation.menu.get("items", [])
        if any(re.match(r"^\s*[A-Za-z]\s+[+#]\s", plain(row.get("text", ""))) for row in rows):
            raise ProtocolError("Pickup menu already has selected items")
        selected = {}
        for key in keys:
            matches = [row for row in rows if (
                ord(key) in row.get("hotkeys", []) or key in row.get("hotkeys", []))]
            if (len(matches) != 1 or matches[0].get("q", 0) <= 0
                    or matches[0].get("level") != 2
                    or matches[0].get("colour") == 8
                    or not re.match(r"^\s*" + re.escape(key) + r"\s+-\s",
                                    plain(matches[0].get("text", "")))):
                raise ProtocolError("Pickup row absent, disabled or ambiguous")
            selected[key] = matches[0].get("text")
        for key in keys:
            current = self.state.observation()
            if current.hostiles or current.menu.get("tag") != "pickup":
                raise ProtocolError("Pickup selection interrupted")
            matches = [row for row in current.menu.get("items", [])
                       if ord(key) in row.get("hotkeys", []) or key in row.get("hotkeys", [])]
            if len(matches) != 1 or matches[0].get("text") != selected[key]:
                raise ProtocolError("Pickup row changed before selection")
            result = await self.key(key)
            if result.dead or result.game_over:
                return result
            matches = [row for row in result.menu.get("items", [])
                       if ord(key) in row.get("hotkeys", []) or key in row.get("hotkeys", [])]
            if len(matches) != 1 or not re.match(
                    r"^\s*" + re.escape(key) + r"\s+\+\s", plain(matches[0].get("text", ""))):
                raise ProtocolError("Native pickup row did not become fully selected")
        current = self.state.observation()
        if current.hostiles or current.menu.get("tag") != "pickup":
            raise ProtocolError("Pickup selection interrupted before commit")
        return await self.key(13)

    def housekeeping(self, observation: Observation) -> Action | None:
        """Resolve only unambiguous UI; unknown prompts cause a safe pause."""
        if observation.dead or observation.game_over:
            return None
        menu = observation.menu
        if observation.input_mode == 5:
            return Action("more", reason="Acknowledge message page", objective="interface")
        if observation.input_mode == 8:
            return Action("confirm", "N", "Decline unsolicited confirmation", "interface")
        if menu.get("type") == "newgame-choice":
            self._expect_fresh_character = True
            return self.character.creation_action(observation)
        if menu.get("tag") == "pickup":
            return None  # Visible item choice belongs to the strategy.
        if self._is_development_menu(observation):
            return None  # The strategy must choose an explicit current-menu item.
        advancement = self.character.advancement_action(observation)
        if advancement is not None:
            return advancement
        if menu:
            return Action("cancel", reason="Close unexpected non-gameplay menu", objective="interface")
        if observation.input_mode in {2, 3, 4}:
            return Action("cancel", reason="Cancel unexpected targeting", objective="interface")
        if observation.input_mode == 7:
            return Action("pause", reason=f"Unrecognised prompt: {observation.prompt}")
        return None

    async def create_character(self) -> Observation:
        for _ in range(30):
            observation = self.state.observation()
            if observation.hp_max and self._is_development_menu(observation):
                # A saved game can resume inside a scroll's native item menu.
                # No further packet arrives until strategy selects its target.
                self._verify_character(observation)
                return observation
            if observation.hp_max and observation.input_mode == 1 and not observation.menu:
                self._verify_character(observation)
                return await self.inspect_inventory()
            if observation.menu.get("type") == "seed-selection":
                if self.seed is None or not self.seed_confirmed:
                    raise ProtocolError("Seed selection requires a verified explicit seed on a fresh account")
                # The native seed field is prefilled from the verified rc. Only
                # accept this specific creation UI; the game banner proves it later.
                await self.key(13)
                continue
            action = self.housekeeping(observation)
            if action is None:
                await self.receive()
            elif action.kind == "pause":
                raise ProtocolError(action.reason)
            else:
                await self.dispatch(action)
        raise ProtocolError("Character creation exceeded 30 UI actions")

    async def inspect_inventory(self) -> Observation:
        """Read the ordinary inventory UI, including its public worn markers."""
        async with self._lock:
            return await self._inspect_inventory()

    async def _inspect_inventory(self) -> Observation:
        observation = self.state.observation()
        if (observation.dead or observation.game_over or observation.input_mode != 1
                or observation.menu or not observation.inventory):
            return observation
        opened = await self.key("i")
        if opened.menu.get("tag") != "inventory":
            raise ProtocolError("Inventory inspection did not open the native inventory menu")
        # Native _send_item compares names rendered against the current global
        # identification table. A newly identified type can therefore change
        # its letter without sending its new name. The inventory UI is
        # authoritative, including the separate potion/scroll/evocable pages.
        rows = []
        seen_pages = set()
        while True:
            if opened.menu.get("tag") != "inventory":
                raise ProtocolError("Inventory paging left the native inventory menu")
            title = plain(self._title(opened))
            paged = "(Left/Right to switch category)" in title
            page = title.split(":", 1)[0]
            if paged:
                if page not in {"Gear", "Potions", "Scrolls", "Evocable Items"}:
                    raise ProtocolError("Unknown native inventory category page")
                if page in seen_pages:
                    break  # Native paging skips empty categories and wraps.
                seen_pages.add(page)
            for entry in opened.menu.get("items", []):
                match = re.match(r"\s*([a-zA-Z])\s+[-+#]\s+(.+)", plain(entry.get("text", "")))
                if match and (ord(match[1]) in entry.get("hotkeys", [])
                              or match[1] in entry.get("hotkeys", [])):
                    rows.append((match[1], match[2], category_from_name(match[2]), entry.get("q")))
            if not paged:
                break
            # CK_RIGHT from the pinned browser key_conversion.js. This changes
            # only the displayed inventory category, never selects an item.
            opened = await self.key(-251)
        for letter, name, category, quantity in rows:
            if category is None or sum(row[0] == letter and row[2] == category for row in rows) != 1:
                continue
            matches = [item for item in opened.inventory if item.letter == letter
                       and (item.category or category_from_name(item.name)) == category]
            if (len(matches) == 1 and (quantity is None or quantity == matches[0].quantity)
                    and public_item_name(matches[0].name) != public_item_name(name)):
                self.state.player["inv"][str(matches[0].slot)]["name"] = name
        # GameState has also consumed the menu's public worn markers.
        return await self.key(27)

    @staticmethod
    def _title(observation: Observation) -> str:
        title = observation.menu.get("title", "")
        return str(title.get("text", "") if isinstance(title, dict) else title)

    @classmethod
    def _is_development_menu(cls, observation: Observation) -> bool:
        return (observation.menu.get("tag") == "use_item"
                and plain(cls._title(observation)) in {
                    "Identify which item? (\\ to view known items)",
                    "Enchant which weapon?", "Enchant which item?"})

    @classmethod
    def _validate_item_choice(cls, observation: Observation, key: str) -> dict:
        if not cls._is_development_menu(observation):
            raise ProtocolError("Expected a supported native item-development menu")
        # Native equipment/supply letters can collide. Validate every menu
        # entry, not the inventory's first matching letter or only eligible rows.
        matches = [entry for entry in observation.menu.get("items", [])
                   if ord(key) in entry.get("hotkeys", []) or key in entry.get("hotkeys", [])]
        if (len(matches) != 1 or matches[0].get("q") == 0
                or matches[0].get("level") == 1 or matches[0].get("colour") == 8
                or not key.isascii() or not key.isalpha()):
            raise ProtocolError("Item choice is absent, ambiguous or unavailable in this menu")
        return matches[0]

    @staticmethod
    def _status_illegal(observation: Observation, kind: str) -> bool:
        if kind in {"menu", "confirm", "more", "cancel"}:
            return False
        status = " ".join((*observation.statuses,
                           *(s.text for s in observation.status_details))).lower()
        berserk = (any(s.lower() in {"berserk", "bearserk"} for s in observation.statuses)
                   or any("berserking" in s.text.lower() for s in observation.status_details))
        if any(word in status for word in ("paralys", "petrified", "asleep")):
            return True
        if kind in {"quaff", "read", "identify", "blink", "ability", "zap", "throw",
                    "wear", "put_on", "inspect", "train_skill"} and berserk:
            return True
        if kind in {"read", "identify", "blink"} and any(word in status for word in ("confus", "silenc", "-scroll")):
            return True
        if kind == "quaff" and "-potion" in status:
            return True
        if kind == "ability" and "silenc" in status:
            return True
        return False

    async def _abilities(self) -> Observation:
        opened = await self.key("a")
        if opened.menu.get("tag") != "ability":
            # No available abilities is a command-mode message, not a menu.
            if opened.input_mode == 1 and not opened.menu:
                self.state.abilities = ()
                return self.state.observation()
            raise ProtocolError("Ability inspection opened an unexpected interface")
        return await self.key(27)

    async def _open_skills(self) -> Observation:
        self.state.skills.clear()
        opened = await self.key("m")
        if opened.menu.get("tag") != "skills" or opened.menu.get("type") != "crt":
            raise ProtocolError("Skill inspection did not open the native skills screen")
        self.state._observe_skills()
        return self.state.observation()

    async def _skill_view(self, name: str) -> Observation:
        # The native ! switch cycles training, cost, targets; no gameplay turns.
        for _ in range(3):
            text = self.state.skill_text()
            heading = {"training": "Train", "targets": "Target"}[name]
            if re.search(rf"\b{heading}\b", text):
                self.state._observe_skills()
                return self.state.observation()
            result = await self.key("!")
            if result.menu.get("tag") != "skills":
                raise ProtocolError("Skill view changed to an unexpected interface")
        raise ProtocolError(f"Native skill {name} view not found")

    async def _inspect_public(self) -> Observation:
        observation = self.state.observation()
        if (observation.dead or observation.game_over or observation.input_mode != 1
                or observation.menu or observation.hostiles):
            raise ProtocolError("Public inspection requires clear command-mode ground")
        await self._inspect_inventory()
        result = await self.key("%")
        if result.menu.get("type") != "formatted-scroller" or result.menu.get("tag") != "resists":
            raise ProtocolError("Overview inspection opened an unexpected interface")
        await self.key(27)
        await self._open_skills()
        await self._skill_view("training")
        await self._skill_view("targets")
        await self.key(27)
        slot = self.state.player.get("weapon_index", -1)
        if slot >= 0:
            result = await self._command({"msg": "inv_item_describe", "slot": slot})
            if result.menu.get("type") != "describe-item":
                raise ProtocolError("Weapon inspection opened an unexpected interface")
            self.state.observe_weapon(slot, result.menu)
            await self.key(27)
        return await self._abilities()

    async def _train_skill(self, value: str) -> Observation:
        name, target_text = value.rsplit(":", 1)
        await self._open_skills()
        if not re.search(r"\bmanual\b", self.state.skill_text()):
            result = await self.key("/")
            if result.menu.get("tag") != "skills" or not re.search(r"\bmanual\b", self.state.skill_text()):
                raise ProtocolError("Manual training mode not confirmed")
        await self._skill_view("training")
        selected = next((s for s in self.state.skills.values() if s.name == name), None)
        if selected is None or not selected.hotkey or selected.level >= float(target_text):
            raise ProtocolError("Selected skill unavailable or target already reached")
        if selected.enabled is False:
            result = await self.key(selected.hotkey)
            if result.menu.get("tag") != "skills":
                raise ProtocolError("Skill selection left the native screen")
            self.state._observe_skills()
            if not self.state.skills[name].enabled:
                raise ProtocolError("Selected skill was not enabled")
        # Focus mode cycles -,+,*; at most two clicks disable another skill.
        for other in tuple(self.state.skills.values()):
            if other.name == name:
                continue
            for _ in range(2):
                current = self.state.skills[other.name]
                if not current.enabled:
                    break
                result = await self.key(current.hotkey)
                if result.menu.get("tag") != "skills":
                    raise ProtocolError("Skill selection left the native screen")
                self.state._observe_skills()
            if self.state.skills[other.name].enabled:
                raise ProtocolError("Other skill remained enabled")
        await self._skill_view("targets")
        result = await self.key("=")
        if result.menu.get("tag") != "skills":
            raise ProtocolError("Target selection left the native skills screen")
        self.state._observe_skills()
        selected = self.state.skills[name]
        result = await self.key(selected.hotkey)
        if result.menu.get("tag") != "skill_target":
            raise ProtocolError("Native skill target editor not observed")
        # Match textinput.js: wipe both sides of the prefill, then submit one
        # native text_input line. Intermediate editor keys need no render reply.
        await self.send({"msg": "key", "keycode": 21})
        await self.send({"msg": "key", "keycode": 11})
        result = await self._command({"msg": "text_input", "text": target_text + "\r"})
        if result.menu.get("tag") != "skills":
            raise ProtocolError("Skill target editor did not return to skills")
        self.state._observe_skills()
        if self.state.skills[name].target != float(target_text):
            raise ProtocolError("Skill target not confirmed by native screen")
        return await self.key(27)

    async def _select_item(self, kind: str, letter: str) -> Observation:
        choices = [i for i in self.state.observation().inventory if i.letter == letter]
        # 0.34 has separate equipment/supply letters; select the public item
        # class relevant to this operation, not an unrelated reused hotkey.
        categories = {"quaff": {"potion"}, "read": {"scroll"}, "zap": {"wand"},
                      "put_on": {"jewellery"}, "throw": {"missile"},
                      "wield": {"weapon", "staff"}, "wear": {"armour"}}
        choices = [i for i in choices
                   if (i.category or category_from_name(i.name)) in categories[kind]]
        if len(choices) != 1:
            raise ProtocolError("Inventory selection is absent or ambiguous")
        if kind in {"wear", "wield", "put_on"} and choices[0].equipped:
            raise ProtocolError("Requested equipment is already equipped")
        prefixes = {"quaff": ("q", "Drink"), "read": ("r", "Read"),
                    "wield": ("w", "Wield"), "wear": ("W", "Wear"),
                    "put_on": ("P", "Put on"), "zap": ("V", "Evoke"),
                    "throw": ("F", "Fire")}
        key, title = prefixes[kind]
        opened = await self.key(key)
        visible = opened.prompt or self._title(opened)
        if (title.lower() not in visible.lower()
                or not (opened.input_mode == 7 or opened.menu.get("tag") in {"inventory", "use_item"})):
            raise ProtocolError(f"{kind} did not open its native selection prompt")
        if opened.menu:
            matches = [entry for entry in opened.menu.get("items", [])
                       if ord(letter) in entry.get("hotkeys", []) or letter in entry.get("hotkeys", [])]
            if (len(matches) != 1 or matches[0].get("q") == 0
                    or matches[0].get("level") == 1 or matches[0].get("colour") == 8
                    or matches[0].get("q", choices[0].quantity) != choices[0].quantity
                    or public_item_name(choices[0].name) != public_item_name(plain(matches[0].get("text", "")))):
                raise ProtocolError("Requested item not present in this native menu")
        return await self.key(letter)

    async def _target(self, observation: Observation, x: int, y: int) -> Observation:
        if observation.input_mode not in {2, 3, 4} or observation.menu:
            raise ProtocolError("Expected native targeting mode not observed")
        if not any(c.visible and (c.x, c.y) == (x, y) for c in observation.cells):
            raise ProtocolError("Target is not currently visible")
        return await self._command({"msg": "click_cell", "x": x, "y": y, "button": 1})

    async def _inspect_monster(self, value: str) -> Observation:
        x, y = map(int, value.split(","))
        observation = self.state.observation()
        monster = next((m for m in observation.monsters if (m.x, m.y) == (x, y)), None)
        if monster is None:
            raise ProtocolError("Monster inspection target is not visible")
        result = await self._command({"msg": "click_cell", "x": x, "y": y, "button": 3})
        examination = None
        if (result.menu.get("tag") == "pickup"
                and plain(self._title(result)) == "What do you want to examine?"):
            # full_describe_square uses an examination-only DescMenu when a
            # monster shares its square with remembered items or a feature.
            # Its tag is also "pickup": never treat that tag as authorization
            # to select an item. Select only this explicitly named monster.
            examination = self.state.menus[-1]
            matches = []
            section = ""
            for entry in result.menu.get("items", []):
                text = plain(entry.get("text", ""))
                if entry.get("level") == 1:
                    section = text
                elif (section == "Monsters" and entry.get("q") == 1
                      and len(entry.get("hotkeys", [])) == 1
                      and monster.name.casefold() in text.casefold()):
                    matches.append(entry)
            if len(matches) != 1:
                raise ProtocolError("Requested monster absent or ambiguous in square examination menu")
            hotkey = matches[0]["hotkeys"][0]
            if isinstance(hotkey, int):
                hotkey = chr(hotkey)
            if len(hotkey) != 1 or not hotkey.isascii() or not hotkey.isalpha():
                raise ProtocolError("Invalid native monster examination hotkey")
            result = await self.key(hotkey)
        if result.menu.get("type") != "describe-monster":
            raise ProtocolError("Monster inspection opened an unexpected interface")
        self.state.observe_monster(monster.id, result.menu)
        result = await self.key(27)
        if examination is not None:
            if not self.state.menus or self.state.menus[-1] is not examination:
                raise ProtocolError("Monster description did not return to its square examination menu")
            if result.menu.get("tag") != "pickup" or plain(self._title(result)) != "What do you want to examine?":
                raise ProtocolError("Monster description returned to an unexpected interface")
            result = await self.key(27)
        return result

    async def dispatch(self, action: Action, *,
                       automation_guard: Callable[[Observation], str] | None = None,
                       automation_delay: float | None = None) -> Observation:
        async with self._lock:
            self._automation_guard = automation_guard
            observation = self.state.observation()
            if observation.dead or observation.game_over:
                raise ProtocolError("Cannot act after game over")
            if action.kind == "pause":
                return observation
            if action.kind == "confirm" and observation.input_mode != 8:
                raise ProtocolError("No confirmation is pending")
            if action.kind == "confirm" and action.value.lower() != "n":
                if (self._save_confirmation_revision != self.state.revision
                        or not re.search(r"\bsav(?:e|ing)\b", observation.prompt, re.IGNORECASE)):
                    raise ProtocolError("Unsolicited affirmative confirmation rejected")
            if action.kind == "menu" and not (observation.menu or observation.input_mode == 7):
                raise ProtocolError("No menu selection is pending")
            if action.kind == "pickup_select" and observation.menu.get("tag") != "pickup":
                raise ProtocolError("No pickup selection is pending")
            if action.kind == "more" and observation.input_mode != 5:
                raise ProtocolError("No message acknowledgement is pending")
            if action.kind not in {"menu", "pickup_select", "confirm", "more", "cancel"} and (
                    observation.input_mode != 1 or observation.menu):
                raise ProtocolError("Gameplay action attempted while a prompt is active")
            if action.kind in {"explore", "travel_downstairs"} and (
                    observation.hostiles or observation.hp_ratio < 1 or observation.statuses):
                if action.kind == "explore":
                    raise ProtocolError("Unsafe auto-exploration rejected")
                raise ProtocolError("Unsafe native stair travel rejected")
            if action.kind == "rest" and observation.hostiles:
                raise ProtocolError("Rest with visible hostiles rejected")
            if action.kind in {"rest", "explore", "travel_downstairs"} and any(
                    (cell.x, cell.y) == observation.position and cell.cloud for cell in observation.cells):
                raise ProtocolError("Native automation in a cloud rejected")
            if action.kind == "travel_downstairs":
                target = tuple(map(int, action.value.split(",")))
                if not any((cell.x, cell.y) == target and cell.feature in DOWNSTAIRS
                           and not cell.cloud for cell in observation.cells):
                    raise ProtocolError("Native stair travel requires known ordinary downstairs")
            if action.kind in {"stairs_up", "stairs_down"} and observation.hostiles:
                raise ProtocolError("Stair traversal with visible hostiles rejected")
            if action.kind == "escape_upstairs":
                if not any((cell.x,cell.y) == observation.position and cell.feature in UPSTAIRS
                           for cell in observation.cells):
                    raise ProtocolError("Return-stair escape requires an observed ordinary upstairs")
                if any(enemy.distance(observation.position) <= 1 for enemy in observation.hostiles):
                    raise ProtocolError("Return-stair escape rejects visible adjacent followers")
            if observation.hostiles and (action.kind in {"wear", "wield", "put_on", "pickup", "train_skill"}
                                         or action.kind == "inspect" and action.value != "abilities"):
                raise ProtocolError("Equipment changes or pickup with visible hostiles rejected")
            if self._status_illegal(observation, action.kind):
                raise ProtocolError("Action prohibited by publicly visible status")
            if observation.hp_max and action.kind not in {"confirm", "more", "cancel"}:
                self._verify_character(observation)
            if action.kind in {"rest", "explore", "travel_downstairs"}:
                try:
                    if action.kind == "travel_downstairs":
                        return await self._travel_downstairs(action.value, automation_guard, automation_delay)
                    return await self._native_automation(action.kind, automation_guard, automation_delay)
                except asyncio.CancelledError:
                    # Reconcile pending native work before releasing the lock.
                    # Prompt cancellation still uses Escape; busy work uses redraw.
                    if ((self.native_activity or self._map_pending)
                            and not (self.state.dead or self.state.game_over)):
                        await self._cancel_native()
                    raise
            if action.kind == "pickup_select":
                return await self._pickup_select(action.value)
            if action.kind == "inspect":
                if action.value == "abilities":
                    return await self._abilities()
                return await self._inspect_public()
            if action.kind == "inspect_monster":
                return await self._inspect_monster(action.value)
            if action.kind == "train_skill":
                return await self._train_skill(action.value)
            if action.kind == "ability":
                names = {"berserk": "Berserk", "trogs_hand": "Trog's Hand",
                         "brothers_in_arms": "Brothers in Arms"}
                opened = await self.key("a")
                if opened.menu.get("tag") != "ability":
                    raise ProtocolError("Ability command did not open native ability menu")
                matches = [a for a in self.state.abilities if a.name == names[action.value]]
                if len(matches) > 1:
                    await self.key(27)
                    raise ProtocolError("Requested ability is ambiguous in the native menu")
                if not matches or not matches[0].available:
                    # Piety/status can change since the last inspection. The
                    # fresh native menu is authoritative; let policy reconsider.
                    return await self.key(27)
                return await self.key(matches[0].hotkey)
            if action.kind in {"zap", "throw", "blink"}:
                if action.kind == "blink" and any("-tele" in s.lower() for s in observation.statuses):
                    raise ProtocolError("Blink prohibited by visible translocation restriction")
                letter, coords = action.value.split(":")
                x, y = map(int, coords.split(","))
                if not any(c.visible and (c.x, c.y) == (x, y) for c in observation.cells):
                    raise ProtocolError("Target is not currently visible")
                if action.kind in {"zap", "throw"}:
                    if not any((m.x, m.y) == (x, y) and m.attitude == 0 for m in observation.monsters):
                        raise ProtocolError("Offensive target is not a visible hostile")
                else:
                    items = [i for i in observation.inventory if i.letter == letter
                             and (i.category or category_from_name(i.name)) == "scroll"
                             and scroll_named(i.name, "blinking")]
                    if len(items) != 1:
                        raise ProtocolError("Blink requires a publicly identified blinking scroll")
                    if not any(c.visible and c.walkable and not c.cloud and (c.x, c.y) == (x, y)
                               for c in observation.cells) or any((m.x, m.y) == (x, y) for m in observation.monsters):
                        raise ProtocolError("Blink destination is not clear visible ground")
                result = await self._select_item("read" if action.kind == "blink" else action.kind, letter)
                return await self._target(result, x, y)
            if action.kind == "identify":
                scroll, target = action.value.split(":")
                items = [i for i in observation.inventory if i.letter == scroll
                         and (i.category or category_from_name(i.name)) == "scroll"
                         and scroll_named(i.name, "identify")]
                if len(items) != 1:
                    raise ProtocolError("Identify requires a publicly identified identify scroll")
                targets = [i for i in observation.inventory if i.letter == target]
                if not targets:
                    raise ProtocolError("Identify target is not present")
                refreshed = await self._inspect_inventory()
                requested = [*items, *targets]
                current = {item.slot: item for item in refreshed.inventory}
                if any(item.slot not in current
                       or current[item.slot].letter != item.letter
                       or current[item.slot].quantity != item.quantity
                       or public_item_name(current[item.slot].name) != public_item_name(item.name)
                       for item in requested):
                    # Identification may have renamed/relettered a type without
                    # a name delta. Reconsider the public refresh before reading.
                    return refreshed
                result = await self._select_item("read", scroll)
                entry = self._validate_item_choice(result, target)
                label = plain(entry.get("text", ""))
                if sum(public_item_name(item.name) == public_item_name(label)
                       and (item.category or category_from_name(item.name)) == category_from_name(label)
                       and entry.get("q", item.quantity) == item.quantity
                       for item in targets) != 1:
                    raise ProtocolError("Requested identify target not present in this native menu")
                return await self.key(target)
            keys = {"wait": ".", "stairs_up": "<", "escape_upstairs": "<", "stairs_down": ">",
                    "pickup": ",", "more": " ", "save": "S"}
            if action.kind in {"quaff", "read", "wield", "wear", "put_on"}:
                if action.kind == "read":
                    items = [i for i in observation.inventory if i.letter == action.value
                             and (i.category or category_from_name(i.name)) == "scroll"]
                    item = items[0] if len(items) == 1 else None
                    if item and any(scroll_named(item.name, name) for name in ("blinking", "identify")):
                        raise ProtocolError("This scroll requires an explicit bounded target action")
                    pending = any(s.lower().startswith("tele") for s in observation.statuses)
                    if item and scroll_named(item.name, "teleportation") and pending:
                        raise ProtocolError("A delayed teleport is already pending")
                if action.kind == "put_on":
                    items = [i for i in observation.inventory if i.letter == action.value
                             and (i.category or category_from_name(i.name)) == "jewellery"]
                    item = items[0] if len(items) == 1 else None
                    if not item:
                        raise ProtocolError("Jewellery selection is not present")
                    ring = bool(re.search(r"\bring(?! mail)\b", item.name.lower()))
                    if not ring and "amulet" not in item.name.lower():
                        raise ProtocolError("Requested item is not publicly described as jewellery")
                    same = [i for i in observation.inventory if i.equipped and (
                            bool(re.search(r"\bring(?! mail)\b", i.name.lower())) if ring
                            else "amulet" in i.name.lower())]
                    if item.equipped or len(same) >= (2 if ring else 1):
                        raise ProtocolError("Jewellery action requires an empty matching slot")
                result = await self._select_item(action.kind, action.value)
                if action.kind in {"wear", "put_on"} and result.input_mode == 1 and not result.menu:
                    result = await self._inspect_inventory()
                return result
            if action.kind == "menu" and observation.menu.get("tag") == "use_item":
                self._validate_item_choice(observation, action.value)
                return await self.key(action.value)
            if action.kind == "menu" and observation.menu.get("tag") == "pickup":
                if observation.hostiles:
                    raise ProtocolError("Pickup selection with visible hostiles rejected")
                hotkeys = [key for entry in observation.menu.get("items", [])
                           for key in entry.get("hotkeys", [])]
                if ord(action.value) not in hotkeys and action.value not in hotkeys:
                    raise ProtocolError("Pickup selection is not present")
                selected = await self.key(action.value)
                if selected.menu.get("tag") == "pickup":
                    return await self.key(13)  # Commit this one explicit selection.
                return selected
            if action.kind == "cancel":
                result = (await self._cancel_native() if self.native_activity or self._map_pending
                          else await self.key(27))
                if (result.input_mode == 8
                        and plain(result.prompt).strip() == "Really abort (and waste the scroll)?"):
                    # This affirmative completes our own explicit cancellation,
                    # not an unsolicited game confirmation. Native y/n can be
                    # uppercase-only even when menu hotkeys advertise both.
                    self.event("scroll_cancel_confirmation", {"prompt": result.prompt})
                    result = await self.key("Y")
                    if result.input_mode == 8 and plain(result.prompt).strip() == "Really abort (and waste the scroll)?":
                        raise ProtocolError("Native scroll cancellation was not completed")
                return result
            if action.kind == "attack":
                # CMD_ATTACK_* uses Ctrl-direction and never moves the player.
                self._save_confirmation_revision = None
                return await self.key(ord(action.value) - ord("a") + 1)
            if action.kind in {"move", "menu", "confirm"}:
                self._save_confirmation_revision = None
                return await self.key(action.value.upper() if action.kind == "confirm" else action.value)
            result = await self.key(keys[action.kind])
            if action.kind == "save" and result.input_mode == 8:
                self._save_confirmation_revision = self.state.revision
            return result

    async def close(self) -> None:
        self._save_confirmation_revision = None
        if self.socket is not None:
            await self.socket.close()
            self.socket = None
