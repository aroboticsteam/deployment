# deployment

Runtime and deployment configuration for [`tmtools`](https://github.com/aroboticsteam/tmtools)
(TMTools, the VEX tournament-management tool). The application repo builds and publishes images;
this repo describes how to run them in a real environment. Nothing here builds from source.

## Contents

| Path | What |
|---|---|
| `compose.stack.yml` + `stack/` | The real stack: `core.yml`, `auth.yml`, `edge.yml`, `extras.yml`, init scripts in `stack/config/` (`gen-secrets.py`, `authelia-init.sh`, ...). Needs Docker Compose >= 2.20 (uses `include:`); keep the directory together. |
| `compose.yml` | Single-file dev stack (app, migrate, Postgres, Redis, MinIO). Copy `.env.example` to `.env` first. |
| `local-testing/` | Throwaway Traefik + Authelia stack for exercising OIDC login locally; not production. |
| `tools/media-ssd/` | LUKS helper and runbook for the encrypted recording SSD. |

## Images

Images come from the application repo's CI (`docker-publish.yml`):

- `ghcr.io/aroboticsteam/tmtools:backend`
- `ghcr.io/aroboticsteam/tmtools:frontend`
- also `<sha>-<component>` and `<version>-<component>` tags.

Override them in `.env` to pin a release or test a custom build:

```bash
TMTOOLS_IMAGE=ghcr.io/aroboticsteam/tmtools:<tag>-backend
FRONTEND_IMAGE=ghcr.io/aroboticsteam/tmtools:<tag>-frontend
```

To test local source, build in the app repo (`docker build -t tmtools-backend:dev .`) and set
`TMTOOLS_IMAGE=tmtools-backend:dev`.

## Running

```bash
python3 stack/config/gen-secrets.py   # or let the one-shot secrets service generate them
docker compose -f compose.stack.yml up -d
```

Configuration is environment-driven (`BASE_DOMAIN`, `REMOTE_APP_HOST`, compose profiles, ...); see
the comments at the top of each file in `stack/`. `REMOTE_APP_HOST` has an `example.org` default,
so set it in `.env` if you route the app on an extra hostname.

Never commit a real `.env`, domain or organisation name.

## Automations as files

Automations, scripts and button pages live in Postgres at runtime. `data/` holds a reviewable copy,
one YAML file per item, and `tools/automation-sync/sync.py` moves it either way through the REST API
using an API key (`TMTOOLS_API_KEY` or `~/.config/tmtools/api-key`):

```bash
pip install -r tools/automation-sync/requirements.txt
export TMTOOLS_URL=https://<your-app-host>
python3 tools/automation-sync/sync.py export            # DB -> data/, then git diff / commit
python3 tools/automation-sync/sync.py import --dry-run  # files -> DB (add --prune to delete extras)
```

Folders are matched by name path, not id; `last_triggered_at` never round-trips. There is no automatic
two-way sync: export overwrites the files, import overwrites the server. This is the seed/restore path
instead of editing the database by hand. Tests: `python3 -m unittest discover tools/automation-sync`.
