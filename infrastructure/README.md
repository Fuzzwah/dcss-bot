# Official local DCSS WebTiles

The engine and browser client are **unmodified Dungeon Crawl Stone Soup 0.34.1**,
from the [official repository](https://github.com/crawl/crawl), pinned to commit
`1eebc1a2892e1c89776a0d7a10691f8dac8d9796`. The bootstrap rejects a different
checkout. `Dockerfile.server` also verifies the commit and pins its Debian base
image digest. Debian security/build packages come from the Debian repositories;
the engine revision is fixed, but package versions are not a bit-for-bit build
lock. The full matching source is retained inside the image at `/opt/crawl`.

## Start with Podman or Docker

From the repository root:

```sh
python3 scripts/server.py
```

This chooses Podman if installed, otherwise Docker, builds the image on the first
run, and runs the foreground container `dcss-webtiles`. The default build uses two
compiler jobs; `--jobs N` changes this. Use `--rebuild` after changing infrastructure
files, or `--build-only` to build without starting. No sudo is needed with working
rootless Podman. Stop with Ctrl-C or `podman stop dcss-webtiles` (use `docker stop`
when running Docker). Do not run two servers against the same state directory.

Open **http://127.0.0.1:8080/**. This is the official WebTiles lobby, not a custom
viewer. Accounts can register with an empty email address. Select an active game
in the lobby to spectate anonymously, or use `http://127.0.0.1:8080/#watch-USERNAME`.
Game IDs are `dcss` (normal) and `dcss-seeded` (native custom-seed mode);
the protocol endpoint is `ws://127.0.0.1:8080/socket`.

### Compose

```sh
mkdir -p state
docker compose up --build -d webtiles
# Alternatively, with a configured Compose provider:
podman compose up --build -d webtiles
```

`compose.yaml` binds **127.0.0.1:8080** only and mounts `./state:/state`. A rootless
Podman container's root user maps to the invoking host user. Docker may create
root-owned state files. Do not make this intentionally simple local account server
public without configuring TLS, authentication policy, resource limits, and
appropriate upstream server hardening.

## Native build and bootstrap (no container)

On Debian/Ubuntu install build prerequisites once:

```sh
sudo apt-get update
sudo apt-get install git build-essential pkg-config bison flex \
  libncursesw5-dev liblua5.4-dev libsqlite3-dev zlib1g-dev libpng-dev \
  python3 python3-venv python-is-python3
python3 scripts/server.py --native
```

This clones into `.runtime/crawl`, creates `.runtime/server-venv`, installs the
upstream pinned Python requirements, builds `WEBTILES=y USE_DGAMELAUNCH=y`, and
executes the official server. Python 3.11 or 3.12 is recommended for the pinned
upstream dependency set. Native mode binds `127.0.0.1` by default. Override
`DCSS_BIND`, `DCSS_PORT`, or `DCSS_STATE` only when intentionally changing native
deployment. `.runtime/` is disposable build state, not game state. If using an
existing checkout, it must be the exact pinned commit. The server launcher writes
only upstream's supported `webserver/config.yml` operator override; it does not
patch the game or its WebTiles implementation.

## Persistence and defaults

`state/` contains:

- `passwd.db3`, `user_settings.db3`: accounts, hashed passwords, WebTiles settings.
- `rc/`: individual rc and macro files, editable through the official lobby.
- `game/`: native save files, logs, and milestones (`USE_DGAMELAUNCH=y` layout).
- `morgues/USERNAME/`: morgues and character dumps.
- `ttyrecs/USERNAME/`: recordings; `running/` and `sockets/`: runtime bookkeeping.

Back up `state/` while stopped. Stopping/restarting the container does not erase
accounts, rc files, saves, or morgues. The same state directory is used by the
native and default container launcher.

`default.rc` is copied only for new accounts and supplies generic UI/safety options:
dangerous-action confirmations, autofight's 70% HP stop, and no implicit auto-explore
resting. It does not install macros, disable confirmations or issue gameplay commands.
The Python controller is responsible for each action.

Character setup is owned by the selected Python profile, not the server image.
Before initial play, the adapter reads the account RC, replaces species/background/
weapon and profile-owned preferences, writes it and verifies exact readback.
MiBe supplies hand-axe and supply-autopickup preferences this way. Unrelated
rc-editor settings are preserved, but profile-owned options are intentionally
overridden; choose `BOT_CHARACTER`/`--character` rather than editing species in RC.
This also overrides legacy MiBe defaults in an already-built server image.
Reconnect does not rewrite RC or recreate the character. Rebuild the image when
deploying the revised generic `default.rc` for new human-created accounts.

The seeded entry uses upstream's `-seed` mode and the same persistent account/save
directory. Its seed-selection UI is prefilled from the per-account verified rc.
The bootstrap sets upstream's supported `CRAWL_DIR` environment default as well
as the game's `dir_path`: WebTiles' `-save-json` helper otherwise inspects a
different directory and fails to advertise saves. A dedicated seeded entry also
ensures that seeded saves are not mislabeled `[slot full]` under normal mode.
Rebuild the image after this configuration change; an already-running container
does not inherit the host's edited bootstrap.

## License

DCSS is [GPL-2.0-or-later](https://github.com/crawl/crawl/blob/0.34.1/LICENSE),
with compatible component licenses and artwork notices detailed in that file.
The image retains `/opt/crawl/LICENSE`, `/opt/crawl/crawl-ref/docs/license/`,
`/opt/crawl/crawl-ref/source/rltiles/license.txt`, all embedded notices, and the
complete matching source. Distributing the image therefore also provides the
corresponding game source; retain these files and notices in derivative images.
No game source is copied into this project's agent package.
