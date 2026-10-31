#!/usr/bin/env python3
"""Build and run pinned DCSS WebTiles locally, with Podman/Docker or natively."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1eebc1a2892e1c89776a0d7a10691f8dac8d9796"
IMAGE = "localhost/dcss-webtiles:0.34.1"


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def native(jobs, rebuild, build_only):
    source = ROOT / ".runtime/crawl"
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--depth", "1", "--branch", "0.34.1",
            "https://github.com/crawl/crawl.git", str(source))
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != COMMIT:
        raise SystemExit(f"Refusing unexpected source revision {revision}; expected {COMMIT}")
    venv = ROOT / ".runtime/server-venv"
    python = venv / "bin/python"
    if not python.exists():
        run(sys.executable, "-m", "venv", str(venv))
        run(str(python), "-m", "pip", "install", "-r", str(source / "crawl-ref/source/webserver/requirements/base.py3.txt"))
    game = source / "crawl-ref/source"
    if rebuild or not (game / "crawl").exists():
        env = os.environ.copy()
        env["PATH"] = str(venv / "bin") + os.pathsep + env["PATH"]
        run("make", "-C", str(game), f"-j{jobs}", "WEBTILES=y", "USE_DGAMELAUNCH=y", env=env)
    if not build_only:
        os.execv(str(python), [str(python), str(ROOT / "infrastructure/run_server.py")])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true", help="use local compiler and Python instead of a container")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the engine/image")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--jobs", type=int, default=2, help="parallel compiler processes (default: 2)")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    os.chdir(ROOT)
    if args.native:
        native(args.jobs, args.rebuild, args.build_only)
        return
    runtime = shutil.which("podman") or shutil.which("docker")
    if not runtime:
        raise SystemExit("Install Podman/Docker, or install the documented native dependencies and use --native.")
    image_exists = subprocess.run([runtime, "image", "inspect", IMAGE], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0
    if args.rebuild or not image_exists:
        image_format = ["--format", "docker"] if Path(runtime).name == "podman" else []
        run(runtime, "build", *image_format, "-f", "Dockerfile.server",
            "--build-arg", f"BUILD_JOBS={args.jobs}", "-t", IMAGE, ".")
    if args.build_only:
        return
    state = ROOT / "state"
    state.mkdir(exist_ok=True)
    os.execv(runtime, [runtime, "run", "--rm", "--name", "dcss-webtiles", "-p", "127.0.0.1:8080:8080",
                      "-v", f"{state}:/state", IMAGE])


if __name__ == "__main__":
    main()
