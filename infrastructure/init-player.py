#!/usr/bin/env python3
"""Initialize persistent paths without replacing a player's edited rc."""
import os
from pathlib import Path
import re
import sys

player = sys.argv[1]
if not re.fullmatch(r"[A-Za-z0-9]{3,20}", player):
    raise SystemExit("Invalid player name")
state = Path(os.environ["DCSS_STATE"])
for directory in (state / "morgues" / player, state / "ttyrecs" / player):
    directory.mkdir(parents=True, exist_ok=True)
try:
    with (state / "rc" / (player + ".rc")).open("x") as target:
        target.write(Path(__file__).with_name("default.rc").read_text())
except FileExistsError:
    pass
