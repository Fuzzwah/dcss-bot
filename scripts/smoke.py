#!/usr/bin/env python3
"""Exercise a running real WebTiles server, controls, persistence and optional video.

Run: uv run --locked --extra video python scripts/smoke.py --record
This creates a fresh local account/save; it never deletes an existing character.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from dcss_bot.cli import control
from dcss_bot.characters import get_character
from dcss_bot.config import Config
from dcss_bot.models import Observation


async def smoke(record: bool, turns: int, username: str | None = None) -> dict:
    character = get_character(Config.from_env().character)
    ident = uuid4().hex[:8]
    username = username or f"Smoke{ident}"
    socket = Path(f".runtime/smoke-{ident}.sock")
    socket.parent.mkdir(exist_ok=True)
    command = [sys.executable, "-m", "dcss_bot.cli", "--control-socket", str(socket),
               "run", "--username", username, "--paused", "--delay", "0.02",
               "--max-turns", str(turns), "--max-actions", str(turns * 4)]
    if record:
        command.append("--record")
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
    output = []

    async def until(predicate):
        while True:
            line = await process.stdout.readline()
            if not line:
                raise RuntimeError("Bot exited before smoke condition: " + "".join(output[-12:]))
            text = line.decode()
            output.append(text)
            if predicate(text):
                return text

    paused_seconds = 0.0

    async def hold_paused():
        nonlocal paused_seconds
        started = time.monotonic()
        await asyncio.sleep(4 if record else .5)
        paused_seconds += time.monotonic() - started

    try:
        await asyncio.wait_for(until(lambda s: s.startswith("BOT_READY")), 90)
        observed_start = time.monotonic()
        initial = await control(socket, "status")
        assert initial["paused"] and initial["ready"], initial
        assert initial["hp"] > 0, initial
        if record:
            await hold_paused()
        stepped = await control(socket, "step")
        assert stepped["paused"] and stepped["actions"] == initial["actions"] + 1, stepped
        await hold_paused()
        stable = await control(socket, "status")
        assert stable["actions"] == stepped["actions"] and stable["turn"] == stepped["turn"], stable
        await control(socket, "resume")
        await asyncio.wait_for(until(lambda s: s.startswith("BOT_STATUS ") and
                                    json.loads(s.removeprefix("BOT_STATUS "))["actions"] >= 6), 45)
        paused = await control(socket, "pause")
        await hold_paused()
        still = await control(socket, "status")
        assert still["actions"] == paused["actions"] and still["turn"] == paused["turn"], still
        await control(socket, "delay", 0)
        await control(socket, "resume")
        async def finish():
            async for line in process.stdout:
                text = line.decode()
                output.append(text)
                if text.startswith("BOT_STATUS "):
                    status = json.loads(text.removeprefix("BOT_STATUS "))
                    if status["paused"] and status["actions"] > paused["actions"]:
                        raise RuntimeError(f"Unexpected pause in {initial['run_dir']}: {status['reason']}")
            await process.wait()
        await asyncio.wait_for(finish(), max(180, turns * 2))
        if process.returncode:
            raise RuntimeError("Bot failed: " + "".join(output)[-6000:])
        observed_elapsed = time.monotonic() - observed_start
        directory = Path(initial["run_dir"])
        summary = json.loads((directory / "summary.json").read_text())
        actions = [json.loads(line) for line in (directory / "actions.jsonl").read_text().splitlines()]
        assert actions and summary["decisions"] == len(actions)
        first = actions[0]["before"]
        identity = Observation(species=first["species"], god=first["god"],
                               messages=tuple(first["messages"]))
        assert character.matches_observation(identity), "Character does not match the selected module"
        if any(message.startswith("Welcome, ") for message in identity.messages):
            assert character.matches_start(identity), "Wrong native starting background"
        meaningful = sum(row["outcome"]["turn_delta"] > 0 for row in actions)
        if not summary["dead"]:
            assert summary["turns_survived"] >= turns, summary["result"]
            assert meaningful >= min(100, turns), meaningful
        evidence = {"username": username, "run_dir": str(directory), "result": summary["result"],
                    "turns": summary["turns_survived"], "meaningful_actions": meaningful,
                    "decisions": summary["decisions"], "kills": summary["kills"],
                    "character": character.identifier,
                    "pause_resume_step": "verified", "video": None}
        if record:
            video = directory / "gameplay.webm"
            assert video.exists() and (directory / "spectator.png").exists(), "Capture failed; inspect events.jsonl"
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(video.resolve().as_uri())
                    await page.wait_for_function("document.querySelector('video')?.readyState >= 2")
                    duration = await page.locator("video").evaluate("v => v.duration")
                    assert duration > 0
                    # Startup, context changes and shutdown count as wall time;
                    # the deliberate idle holds must not count as video time.
                    assert duration + paused_seconds <= observed_elapsed + 2, {
                        "duration": duration, "paused_seconds": paused_seconds,
                        "observed_wall_seconds": observed_elapsed}
                    evidence["video"] = {
                        "path": str(video), "duration_seconds": duration, "decoded_by": "Chromium",
                        "paused_hold_seconds": paused_seconds,
                        "observed_wall_seconds": observed_elapsed, "pause_time_excluded": True}
                finally:
                    await browser.close()
        (directory / "smoke.json").write_text(json.dumps(evidence, indent=2) + "\n")
        return evidence
    finally:
        if process.returncode is None:
            try:
                await control(socket, "stop")
                await asyncio.wait_for(process.communicate(), 45)
            except (OSError, RuntimeError, TimeoutError):
                process.terminate()
                await asyncio.wait_for(process.communicate(), 20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--turns", type=int, default=130)
    parser.add_argument("--username", help="Resume a specific smoke account instead of creating a fresh one")
    args = parser.parse_args()
    if args.turns < 1:
        parser.error("--turns must be positive")
    print(json.dumps(asyncio.run(smoke(args.record, args.turns, args.username)), indent=2))


if __name__ == "__main__":
    main()
