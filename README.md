# deployment

Runtime and deployment configuration for [`vex-tm-tools`](https://github.com/aroboticsteam/vex-tm-tools)
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

- `ghcr.io/aroboticsteam/vex-tm-tools:backend`
- `ghcr.io/aroboticsteam/vex-tm-tools:frontend`
- also `<sha>-<component>` and `<version>-<component>` tags.

Override them in `.env` to pin a release or test a custom build:

```bash
TMTOOLS_IMAGE=ghcr.io/aroboticsteam/vex-tm-tools:<tag>-backend
FRONTEND_IMAGE=ghcr.io/aroboticsteam/vex-tm-tools:<tag>-frontend
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
