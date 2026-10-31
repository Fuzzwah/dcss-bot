#!/usr/bin/env python3
"""Configure and exec the unmodified upstream WebTiles server."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("DCSS_SOURCE", ROOT / ".runtime/crawl")).resolve()
STATE = Path(os.environ.get("DCSS_STATE", ROOT / "state")).resolve()
SERVER = SOURCE / "crawl-ref/source"
INFRA = Path(__file__).resolve().parent


def main():
    os.environ["DCSS_STATE"] = str(STATE)
    # Upstream's -save-json helper does not inherit a game's -dir argument.
    # The supported environment default makes lobby save inspection agree with play.
    os.environ["CRAWL_DIR"] = str(STATE / "game")
    os.environ.setdefault("LANG", "C.UTF-8")
    for directory in ("rc", "morgues", "game", "running", "ttyrecs", "sockets"):
        (STATE / directory).mkdir(parents=True, exist_ok=True)
    (INFRA / "init-player.py").chmod(0o755)
    config = {
        "bind_address": os.environ.get("DCSS_BIND", "127.0.0.1"),
        "bind_port": int(os.environ.get("DCSS_PORT", "8080")),
        "dgl_mode": True,
        "password_db": str(STATE / "passwd.db3"),
        "settings_db": str(STATE / "user_settings.db3"),
        "init_player_program": str(INFRA / "init-player.py"),
        "new_accounts_disabled": False,
        "new_accounts_hold": False,
        "allow_password_reset": False,
        "allow_anon_spectate": True,
        "crypt_algorithm": "6",
        "crypt_salt_length": 16,
        "use_game_yaml": False,
        "enable_ttyrecs": True,
        "server_id": "local-dcss-0.34.1",
        "games": {
            "dcss": {
                "id": "dcss",
                "name": "DCSS 0.34.1",
                "version": "0.34.1",
                "crawl_binary": str(SERVER / "crawl"),
                "rcfile_path": str(STATE / "rc"),
                "macro_path": str(STATE / "rc"),
                "morgue_path": str(STATE / "morgues/%n"),
                "dir_path": str(STATE / "game"),
                "socket_path": str(STATE / "sockets"),
                "inprogress_path": str(STATE / "running"),
                "ttyrec_path": str(STATE / "ttyrecs/%n"),
                "client_path": str(SERVER / "webserver/game_data"),
                "show_save_info": True,
            }
        },
    }
    config["games"]["dcss-seeded"] = {
        **config["games"]["dcss"], "id": "dcss-seeded",
        "name": "DCSS 0.34.1 seeded", "options": ["-seed"],
    }
    # JSON is valid YAML; this is upstream's supported operator override file.
    (SERVER / "webserver/config.yml").write_text(json.dumps(config, indent=2) + "\n")
    os.chdir(SERVER)
    os.execv(sys.executable, [sys.executable, str(SERVER / "webserver/server.py")])


if __name__ == "__main__":
    main()
