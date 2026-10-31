"""Single-flight WebTiles adapter; no strategy and no hidden state queries."""
import asyncio
import hashlib
import json
import re
import zlib
from collections.abc import Callable
from html.parser import HTMLParser

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .characters.base import CharacterProfile
from .config import validate_seed
from .models import Action, Observation
from .state import GameState


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
            if observation.hp_max and (observation.input_mode != 0 or observation.menu):
                self._verify_character(observation)
                return await self.inspect_inventory()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("No saved-character state after reconnect")
            await self.receive(remaining)

    async def key(self, key: str | int) -> Observation:
        deadline = asyncio.get_running_loop().time() + self.timeout
        await self.send({"msg": "key", "keycode": ord(key) if isinstance(key, str) else key})
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

    def housekeeping(self, observation: Observation) -> Action | None:
        """Resolve only unambiguous UI; unknown prompts cause a safe pause."""
        if observation.dead or observation.game_over:
            return None
        menu = observation.menu
        if observation.input_mode == 5:
            return Action("more", reason="Acknowledge message page", objective="interface")
        if observation.input_mode == 8:
            return Action("confirm", "n", "Decline unsolicited confirmation", "interface")
        if menu.get("type") == "newgame-choice":
            self._expect_fresh_character = True
            return self.character.creation_action(observation)
        if menu.get("tag") == "pickup":
            return None  # Visible item choice belongs to the strategy.
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
        # GameState consumes visible item labels as menu chunks arrive.
        return await self.key(27)

    async def dispatch(self, action: Action) -> Observation:
        async with self._lock:
            observation = self.state.observation()
            if observation.dead or observation.game_over:
                raise ProtocolError("Cannot act after game over")
            if action.kind == "pause":
                return observation
            if action.kind == "confirm" and observation.input_mode != 8:
                raise ProtocolError("No confirmation is pending")
            if action.kind == "menu" and not (observation.menu or observation.input_mode == 7):
                raise ProtocolError("No menu selection is pending")
            if action.kind == "more" and observation.input_mode != 5:
                raise ProtocolError("No message acknowledgement is pending")
            if action.kind not in {"menu", "confirm", "more", "cancel"} and (
                    observation.input_mode != 1 or observation.menu):
                raise ProtocolError("Gameplay action attempted while a prompt is active")
            if action.kind == "explore" and (observation.hostiles or observation.hp_ratio < .95 or observation.statuses):
                raise ProtocolError("Unsafe auto-exploration rejected")
            if action.kind in {"stairs_up", "stairs_down"} and observation.hostiles:
                raise ProtocolError("Stair traversal with visible hostiles rejected")
            if action.kind in {"wear", "wield", "pickup"} and observation.hostiles:
                raise ProtocolError("Equipment changes or pickup with visible hostiles rejected")
            if observation.hp_max and action.kind not in {"confirm", "more", "cancel"}:
                self._verify_character(observation)
            prefixes = {"quaff": "q", "read": "r", "wield": "w", "wear": "W", "ability": "a"}
            keys = {"wait": ".", "explore": "o", "stairs_up": "<", "stairs_down": ">",
                    "pickup": ",", "cancel": 27, "more": " ", "save": "S"}
            if action.kind in prefixes:
                if action.kind != "ability" and not any(i.letter == action.value for i in observation.inventory):
                    raise ProtocolError("Inventory selection is not present")
                next_obs = await self.key(prefixes[action.kind])
                if next_obs.input_mode == 1 and not next_obs.menu:
                    raise ProtocolError(f"{action.kind} did not open a selection prompt")
                result = await self.key(action.value)
                if action.kind == "wear":
                    result = await self._inspect_inventory()
                return result
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
            if action.kind in {"move", "menu", "confirm"}:
                return await self.key(action.value)
            return await self.key(keys[action.kind])

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()
            self.socket = None
