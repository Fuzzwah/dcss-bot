"""Small environment-driven configuration; credentials never enter run metadata."""
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import secrets

from .characters import DEFAULT_CHARACTER, get_character


DCSS_VERSION = "0.34.1"
DCSS_COMMIT = "1eebc1a2892e1c89776a0d7a10691f8dac8d9796"


def validate_seed(seed: int | None) -> None:
    if seed is not None and (type(seed) is not int or not 1 <= seed <= 2**64 - 1):
        raise ValueError("BOT_SEED must be an integer between 1 and 18446744073709551615; 0 is random")


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid .env entry on line {number}")
        os.environ.setdefault(key, value.strip().strip("\"'"))


@dataclass
class Config:
    url: str = "ws://127.0.0.1:8080/socket"
    username: str = "MiBeBot"
    password: str = ""
    game_id: str = "dcss"
    seeded_game_id: str = "dcss-seeded"
    delay: float = 0.4
    explore_delay: float = 0.02
    max_actions: int = 10000
    max_turns: int = 10000
    stall_limit: int = 24
    timeout: float = 15.0
    run_root: Path = Path("runs")
    control_socket: Path = Path("state/control.sock")
    record_video: bool = False
    paused: bool = False
    agent: str = "rule"
    character: str = DEFAULT_CHARACTER
    seed: int | None = None

    @classmethod
    def from_env(cls) -> "Config":
        load_env()
        return cls(
            url=os.getenv("DCSS_URL", cls.url), username=os.getenv("BOT_USERNAME", cls.username),
            password=os.getenv("BOT_PASSWORD", ""), game_id=os.getenv("DCSS_GAME_ID", cls.game_id),
            seeded_game_id=os.getenv("DCSS_SEEDED_GAME_ID", cls.seeded_game_id),
            delay=float(os.getenv("ACTION_DELAY", "0.4")),
            explore_delay=float(os.getenv("EXPLORE_DELAY", "0.02")),
            max_actions=int(os.getenv("MAX_ACTIONS", "10000")),
            max_turns=int(os.getenv("MAX_TURNS", "10000")),
            run_root=Path(os.getenv("RUN_DIR", "runs")),
            control_socket=Path(os.getenv("CONTROL_SOCKET", "state/control.sock")),
            record_video=os.getenv("RECORD_VIDEO", "false").lower() in {"true", "1", "yes"},
            agent=os.getenv("BOT_AGENT", "rule"),
            character=os.getenv("BOT_CHARACTER", DEFAULT_CHARACTER),
            seed=int(os.environ["BOT_SEED"]) if os.getenv("BOT_SEED") else None,
        )

    def validate(self) -> None:
        validate_seed(self.seed)
        get_character(self.character)
        if not self.url.startswith(("ws://", "wss://")):
            raise ValueError("DCSS_URL must be a WebSocket URL")
        if not re.fullmatch(r"[A-Za-z0-9]{3,20}", self.username):
            raise ValueError("BOT_USERNAME must contain 3–20 ASCII letters/digits")
        if not math.isfinite(self.delay) or self.delay < 0 or self.delay > 60:
            raise ValueError("Action delay must be between 0 and 60 seconds")
        if isinstance(self.explore_delay, bool) or not math.isfinite(self.explore_delay) or not 0 <= self.explore_delay <= 2:
            raise ValueError("Explore delay must be between 0 and 2 seconds")
        if self.max_actions < 1 or self.max_turns < 1 or self.stall_limit < 1:
            raise ValueError("Run limits must be positive")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("Timeout must be finite and positive")

    def ensure_credentials(self) -> None:
        if self.password:
            return
        directory = self.control_socket.parent
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"credentials-{self.username}.json"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            data = json.loads(path.read_text())
            self.password = data["password"]
        else:
            self.password = secrets.token_urlsafe(24)
            with os.fdopen(fd, "w") as handle:
                json.dump({"username": self.username, "password": self.password}, handle)

    def public_dict(self) -> dict:
        result = asdict(self)
        result.pop("password")
        result["run_root"] = str(self.run_root)
        result["control_socket"] = str(self.control_socket)
        result["dcss_version"] = DCSS_VERSION
        result["dcss_commit"] = DCSS_COMMIT
        result["character"] = {"module": self.character, **get_character(self.character).metadata()}
        return result
