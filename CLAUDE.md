# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the application

**Development (local):**
```bash
# Activate venv first
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux/Mac

# Web server — accessible at http://localhost:5000 (no /camwatch prefix in dev)
python wsgi.py

# Checker daemon (separate terminal)
python checker/service.py
```

**Production (VPS):**
```bash
bash deploy.sh   # git pull + pip install + systemctl restart
```

The two systemd services are `camwatch-web` (Gunicorn on port 5005) and `camwatch-checker` (checker daemon).

## Environment setup

Copy `env.example` to `.env` and fill in the values. Required variables: `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_NAME`. Optional: `DB_PORT` (default 3306), `SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `CHECKER_WORKERS` (default 80 threads), `CHECKER_LOOP_SLEEP` (default 10s), `CHECKER_TIMEOUT_SEC` (default 20s), `MASTER_PASSWORD` (grants access to all companies in the monitoring screens — if empty, master access is disabled).

Database schema is in `_doc/_db/db.sql` — apply it manually to a fresh MySQL database. SQLAlchemy does **not** manage migrations; schema changes must be applied by hand.

## Architecture

CamWatch runs as **two independent processes** that share the same MySQL database:

### Web process (`wsgi.py` → `app/`)

Flask app created via `create_app()` factory. In production, `DispatcherMiddleware` mounts it at `/camwatch`, so all `url_for()` calls, redirects, and HTMX targets must remain relative — never hardcode the path prefix. In development, `_app.run(debug=True)` bypasses the middleware and runs at root.

Two blueprints:
- `monitor_bp` — mounted at `/` — the four monitoring screens plus access control routes (`/acesso`, `/sair`)
- `cadastro_bp` — mounted at `/cadastro` — CRUD for empresas, grupos, cameras; includes `/cameras/<id>/toggle` (POST) to activate/deactivate a camera directly from the listing without going through the edit form

**HTMX pattern**: Full-page routes render complete templates; corresponding `/parcial` or `/detalhe/<id>` routes return only the relevant fragment. The partial endpoints share query logic with their parent full-page routes via private helper functions (`_query_eventos`, `_query_cameras_polaroid`, etc.).

Four monitoring screens:
- **Monitor** (`/`) — paginated event log with filter bar (empresa / grupo / câmera); auto-refreshes summary cards via HTMX polling (`/resumo/parcial`); filter/pagination updates the event table via `/eventos/parcial` (no full reload)
- **Fora do ar** (`/fora-do-ar`) — table of cameras that have been continuously offline longer than `NOTIF_THRESHOLD_SEC`; the count header reads "N câmeras fora do ar há mais de X segundos" where X is `NOTIF_THRESHOLD_SEC` passed as `threshold_sec` to both the full route and the partial; filters by empresa and grupo; user-selectable auto-refresh interval (60 / 120 / 300 s) controlled via JS `setInterval` + `htmx.trigger(wrapper, 'refresh')`; partial endpoint `/fora-do-ar/parcial`; rows ordered newest-offline-first
- **Polaroid** (`/polaroid`) — card grid showing current status of every camera; filters by empresa, grupo, and **status** (all / online / offline); each offline card shows how long the camera has been down (`offline_desde` from a secondary `MAX(timestamp)` query); refreshes the camera grid via HTMX polling (`/polaroid/parcial`)
- **Números** (`/numeros`) — per-empresa statistics for the last 120 hours; uses `LEAD()` window function to read `duracao_offline_segundos` from the subsequent online event; no filter controls on this screen

### Access control (session-based PIN)

Both `monitor_bp` and `cadastro_bp` have a `before_request` guard that redirects unauthenticated requests to `/acesso`. Each blueprint defines its own `_empresa_restrita()` helper with identical logic.

- `/acesso` (GET/POST) — PIN entry page (no sidebar, standalone layout). Accepts either a company 6-digit PIN or the `MASTER_PASSWORD` from `.env`.
- `/sair` (GET) — clears the session and redirects to `/acesso`.

Session keys set on login:
- `session["empresa_acesso"]` — `"*"` for master access, or an `int` (empresa_id) for company-restricted access.
- `session["empresa_nome"]` — display name shown in the sidebar (`"Acesso total"` or the empresa name).

**Company-restricted sessions**: when `session["empresa_acesso"]` is an int, `_empresa_restrita()` returns that id and all queries automatically filter to that empresa. This applies to both monitoring and cadastro:

- **Monitor**: eventos, resumo cards, fora do ar table, polaroid grid, números all filter by empresa. The empresa dropdown is replaced by a hidden input in filter forms. `/numeros/detalhe/<empresa_id>` returns 403 if the id doesn't match the session.
- **Cadastro**: the "Empresas" sub-nav link is hidden; empresa CRUD routes return 403. Grupos and Câmeras listings show only records belonging to the restricted empresa. Create/edit forms replace the empresa dropdown with a static text display + hidden input. Edit/delete/toggle operations on grupos and câmeras return 403 if the record belongs to a different empresa.

**Nav link for Cadastros** (`base.html`): the sidebar link points to `cadastro.grupos` for company-restricted sessions and to `cadastro.empresas` for master sessions. This avoids a 403 on the landing page when a restricted user clicks "Cadastros".

**Master sessions**: `_empresa_restrita()` returns `None` and all data is visible with no forced filter; the full Cadastro menu (including Empresas) is shown.

**`empresa.senha`**: stored as `CHAR(6)` (nullable) in the `empresa` table to preserve leading zeros. Set via the empresa edit form in Cadastros. If `NULL`, that empresa cannot be accessed by PIN (only via master password). Migration for existing databases: `ALTER TABLE empresa ADD COLUMN senha CHAR(6) NULL AFTER ativo;`

### Checker process (`checker/service.py`)

> **Note:** The checker's log file is hardcoded to `/var/log/camwatch_checker.log` (Linux/production path). On Windows (dev), the `FileHandler` will fail to open; redirect or remove it locally if needed.

Infinite loop:
1. Query cameras whose per-camera `intervalo_segundos` has elapsed (`get_cameras_due`)
2. Run `ffprobe` (must be installed on the system) in parallel via `ThreadPoolExecutor` — no video decoding, just stream probe
3. Apply a **3-failure debounce** before marking a camera offline (counter in `_falhas` dict, resets on any online result)
4. On status change: update `camera.ultimo_status`, insert a row into `evento_camera`
5. When a camera returns online: compute `duracao_offline_segundos` from the last offline event timestamp and write it to the new online event row
6. After `session.commit()`: send Telegram notifications according to the delay rules below

**Telegram notification delay**: offline alerts are only sent after the camera has been continuously offline for `NOTIF_THRESHOLD_SEC` (default 600 s / 10 min, configurable via `.env`). The checker reads this value from `Config.NOTIF_THRESHOLD_SEC` and stores it locally as `_NOTIF_THRESHOLD_SEC`. The web process also reads `Config.NOTIF_THRESHOLD_SEC` to drive the "Fora do ar" screen. The checker tracks state with two in-memory dicts: `_offline_desde` (camera_id → datetime when offline was confirmed) and `_offline_notificado` (camera_id → whether the alert was already sent). Recovery notifications are only sent if the offline alert was previously dispatched. On startup, `_init_offline_desde()` reconstructs this state from the database to avoid duplicate alerts after a process restart.

### Data model

- `empresa` → `grupo_camera` (many) → `camera` (many) → `evento_camera` (many, append-only)
- `empresa.senha` (`CHAR(6) NULL`) — 6-digit numeric PIN for session-based access control; `NULL` means no PIN configured
- `evento_camera` only records **state changes**, not every check. `duracao_offline_segundos` is populated on the *online* event (not the offline one) when the camera recovers.
- All datetimes are stored as naive `DATETIME` in São Paulo time (`America/Sao_Paulo`). The `_agora()` helper in `models.py` and `_SP` timezone objects throughout enforce this consistently.
- `camera.ultimo_status` is a denormalized cache of the latest event status — avoid querying `evento_camera` for current status; use `camera.ultimo_status` instead.

### Telegram notifications

Notifications are per `grupo_camera`, not per camera. Set `grupo_camera.telegram` to a Telegram chat ID. The bot token comes from `TELEGRAM_BOT_TOKEN` in `.env`. If either is absent, notifications are silently skipped.

### Filter persistence

The **Monitor**, **Fora do ar**, and **Polaroid** screens use a `syncFiltros()` JavaScript function that pushes the active filter values into the URL via `history.replaceState()` whenever the form changes. This ensures filters survive page refreshes and that the HTMX polling target (`hx-get`) always carries the current filter query string. The Números screen has no filter bar.

### Números screen — per-camera detail

`/numeros/detalhe/<empresa_id>` is an HTMX endpoint that returns a per-camera breakdown (offline count, average duration, bucketed by <3 min / 3-10 min / +10 min, mutually exclusive) for the selected empresa. It uses the same `LEAD()` CTE as the parent `/numeros` route and renders `partials/numeros_detalhe.html`.
