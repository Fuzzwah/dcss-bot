"""Fish-friendly command line; local controls use a private Unix socket."""
import argparse
import asyncio
import json
from pathlib import Path
import signal
import sys

from .adapter import WebTilesAdapter
from . import agents
from .characters import get_character
from .config import Config


async def control(path: Path, command: str, value: float | None = None) -> dict:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 3)
    except (OSError, asyncio.TimeoutError) as exc:
        raise RuntimeError(f"No bot control socket at {path}. Start with dcss-bot run.") from exc
    try:
        payload = {"command": command}
        if value is not None:
            payload["value"] = value
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), 45)
        if not line:
            raise RuntimeError("Bot closed the control connection without a response")
        return json.loads(line)
    finally:
        writer.close()
        await writer.wait_closed()


async def run(config: Config) -> dict:
    from .recording import Recorder
    from .supervisor import Supervisor

    if config.seed is not None:
        config.game_id = config.seeded_game_id
    config.validate()
    character = get_character(config.character)
    agent = agents.make_agent(config.agent, character)
    config.ensure_credentials()
    recorder = Recorder(config.run_root, config.public_dict())
    adapter = WebTilesAdapter(config.url, config.username, config.password,
                             config.game_id, config.timeout, recorder.event,
                             character=character, seed=config.seed)
    supervisor = Supervisor(config, agent, adapter, recorder)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, supervisor.request_stop)
    try:
        return await supervisor.run()
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Autonomous DCSS character modules with native WebTiles spectating")
    root.add_argument("--control-socket", type=Path, help="Override the local control socket")
    commands = root.add_subparsers(dest="command", required=True)
    start = commands.add_parser("run", help="Create or resume the bot's saved character")
    start.add_argument("--delay", type=float, help="Seconds between decisions (0 accelerates)")
    start.add_argument("--username", help="Separate account/save slot for a new experiment")
    start.add_argument("--seed", type=int, help="Fixed uint64 seed; requires a fresh account (0 is random and rejected)")
    start.add_argument("--max-actions", type=int)
    start.add_argument("--max-turns", type=int)
    start.add_argument("--agent", help="rule or installed module:Class")
    start.add_argument("--character", help="Character module: mibe or installed module:PROFILE")
    start.add_argument("--record", action="store_true", help="Record an anonymous spectator as WebM")
    start.add_argument("--paused", action="store_true", help="Start paused after character creation")
    for command in ("status", "pause", "resume", "step", "stop"):
        commands.add_parser(command)
    delay = commands.add_parser("delay", help="Change pacing without restarting")
    delay.add_argument("seconds", type=float)
    inspect = commands.add_parser("inspect", help="Print a run's metadata and final/live status")
    inspect.add_argument("run_directory", type=Path)
    evaluate = commands.add_parser("evaluate", help="Run isolated fresh-account trials on fixed seeds")
    evaluate.add_argument("--seeds", type=int, nargs="+", required=True)
    evaluate.add_argument("--agents", nargs="+", default=["rule"], help="rule or installed module:Class identifiers")
    evaluate.add_argument("--character", help="Character module: mibe or installed module:PROFILE")
    evaluate.add_argument("--repeats", type=int, default=1)
    evaluate.add_argument("--max-turns", type=int, default=1000)
    evaluate.add_argument("--max-actions", type=int, default=4000)
    evaluate.add_argument("--seconds-per-run", type=float, default=600)
    evaluate.add_argument("--delay", type=float, default=0)
    evaluate.add_argument("--record", action="store_true")
    evaluate.add_argument("--output-root", type=Path, help="Default: RUN_DIR/evaluations")
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        config = Config.from_env()
        if args.control_socket:
            config.control_socket = args.control_socket
        if args.command == "run":
            for key in ("delay", "username", "seed", "max_actions", "max_turns", "agent", "character"):
                value = getattr(args, key)
                if value is not None:
                    setattr(config, key, value)
            config.record_video = config.record_video or args.record
            config.paused = args.paused
            result = asyncio.run(run(config))
        elif args.command == "evaluate":
            from .evaluation import Evaluation, run_evaluation
            if args.character is not None:
                config.character = args.character
            plan = Evaluation(tuple(args.seeds), tuple(args.agents), args.repeats,
                              args.max_turns, args.max_actions, args.seconds_per_run, args.delay, args.record)
            result = asyncio.run(run_evaluation(config, plan, args.output_root))
        elif args.command == "inspect":
            directory = args.run_directory
            result = {name: json.loads((directory / name).read_text())
                      for name in ("manifest.json", "report.json", "metadata.json", "summary.json", "status.json")
                      if (directory / name).exists()}
            if not result:
                raise ValueError(f"No recorded run at {directory}")
        else:
            result = asyncio.run(control(config.control_socket, args.command, getattr(args, "seconds", None)))
        display = {k: v for k, v in result.items() if k != "final_observation"} if args.command == "run" else result
        print(json.dumps(display, indent=2, ensure_ascii=False))
        if isinstance(result, dict) and (result.get("error") or result.get("result") == "error"):
            sys.exit(1)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"dcss-bot: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
