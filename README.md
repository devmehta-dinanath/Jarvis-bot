# Screenpipe Backend

FastAPI microservice for a Screenpipe-style OCR pipeline:

- FastAPI for REST APIs
- SQLite for job and frame metadata
- SQLAlchemy for database access
- **screenpipe** service — runs `screenpipe record` and syncs frames when the screen changes
- **media** service — stores video, frames, and paths per recording
- **paddle_ocr** service — runs OCR on each frame and saves results

## Project structure

```text
app/
  bootstrap.py
  config.py
  database.py
  crud.py
  main.py
  models.py
  schemas.py
  recording_paths.py  # media/recording_<id>/{screenpipe,frames,ocr} path helpers
  video_frames.py     # extract frames from video (batch API jobs)
  frame_cleanup.py    # rolling JPG retention after Chroma index
  services/
    manager.py          # starts all background services on server boot
    pipeline.py         # one-shot jobs from POST /recordings/start
    screenpipe/
      cli.py            # starts `screenpipe record`
      client.py         # polls Screenpipe API (localhost:3030)
      capture.py        # ffmpeg fallback / API video jobs
      service.py        # syncs new frames into media/ + DB
    paddle_ocr/
      engine.py         # PaddleOCR wrapper
      processor.py      # OCR one frame, write .txt under ocr/
      service.py        # background worker for queued frames
requirements.txt
data/
  jarvis.db           # SQLite DB (created on first server start; open in VS Code)
media/
  recording_<id>/
    screenpipe/         # capture.mp4 and raw capture output
    frames/             # frame_000001.jpg, ...
    ocr/                # frame_000001.txt (OCR text when done)
```

## Deployment (server + desktop)

Jarvis runs as **two roles**:

| Role | Machine | Script | What runs |
|------|---------|--------|-----------|
| **Server** | VPS / always-on Linux | `./scripts/server-deploy.sh` | Central DB, WhatsApp, Google Calendar, AI summaries, sync API |
| **Desktop client** | Linux laptop/desktop | `./scripts/desktop-client-setup.sh --server-url URL` | Screenpipe, OCR, activity capture → syncs to server |

```text
  Desktop (client)                    Server (VPS)
  Screenpipe + OCR        ──sync──►   jarvis.db (central)
  client-buffer.db (local queue)      WhatsApp + Calendar + AI
```

### Server setup

```bash
cp .env.server.example .env   # or let server-deploy.sh create it
./scripts/server-deploy.sh
```

- Health: http://127.0.0.1:8000/health
- Give desktop users: `http://<server-ip>:8000`
- Stop: `docker compose -f docker-compose.server.yml down`

### Desktop client setup

```bash
./scripts/desktop-client-setup.sh --server-url http://YOUR_SERVER_IP:8000
```

Anyone can run this on a Linux desktop with Docker. It installs Docker if missing, clones the repo (with `--clone URL`), and starts the capture container.

- Local status: http://127.0.0.1:8000/api/v1/services/status
- Stop: `docker compose -f docker-compose.client.yml down`

### Frontend (jarvis-bot-fe)

Point the Electron app at the **server**, not the desktop:

```bash
# jarvis-bot-fe/.env
JARVIS_API_URL=http://YOUR_SERVER_IP:8000
```

### Config templates

| File | Role |
|------|------|
| `.env.server.example` | Server — API keys, WhatsApp, `SYNC_API_KEY` |
| `.env.client.example` | Desktop — `JARVIS_SERVER_URL`, Screenpipe, same `SYNC_API_KEY` |

### Database note

- **Server** owns `data/jarvis.db` — the only long-term database.
- **Desktop** uses `data/client-buffer.db` locally while uploading; it never writes to the server's DB directly.
- For multi-user scale, set `DATABASE_URL=postgresql://...` on the server.

### Local development (both roles on one machine)

```bash
# Terminal 1 — server
cp .env.server.example .env
docker compose -f docker-compose.server.yml up --build

# Terminal 2 — client
cp .env.client.example .env
# set JARVIS_SERVER_URL=http://127.0.0.1:8000 and matching SYNC_API_KEY
./scripts/desktop-client-setup.sh --server-url http://127.0.0.1:8000
```

## Data folders

| Path | Server | Desktop client |
|------|--------|----------------|
| `data/jarvis.db` | Central database | — |
| `data/client-buffer.db` | — | Local upload queue |
| `data/chroma/` | Vector search index | — |
| `media/recording_<id>/` | Synced metadata (optional images) | Local frame captures |

**Do not run server and client on the same port without Docker** — both use `:8000` by default inside their containers.

## Screenpipe CLI (desktop client only)

No ffmpeg interval config. On startup this backend:

1. Runs **`screenpipe record`** (same as `npx screenpipe@latest record`)
2. Screenpipe captures **only when the screen changes** (app switch, click, typing, etc.)
3. Polls **http://127.0.0.1:3030** for new frames
4. Copies images to `media/recording_<id>/frames/` and queues **paddle_ocr**

Requires **Node.js** (`npx`) or the `screenpipe` binary on your PATH.

```bash
# optional: run Screenpipe alone first to verify
npx -y screenpipe@latest record
```

### Docker compose files

| File | Role |
|------|------|
| `docker-compose.server.yml` | Server stack |
| `docker-compose.client.yml` | Desktop capture stack |
| `Dockerfile.server` | Lightweight API image (no Screenpipe/Paddle) |
| `Dockerfile.client` | Full capture image |

### Local (venv, no Docker)

```bash
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Starts `screenpipe record` automatically (`SCREENPIPE_START_CLI=true` by default).

API: http://127.0.0.1:8000 · Status: http://127.0.0.1:8000/api/v1/services/status

### Media folder empty?

```bash
curl http://127.0.0.1:8000/api/v1/services/status
```

Check `"screenpipe": {"cli_running": true}` and `"screenpipe_api_url"`. Then **switch apps or click** — Screenpipe is event-driven, not continuous video.

If Screenpipe API is down, run manually: `screenpipe record` or `npx -y screenpipe@latest record`.

### Open SQLite in VS Code / Cursor

1. Start the server once so the DB is created: `uvicorn app.main:app --reload`
2. In the explorer, open **`data/jarvis.db`**
3. If prompted, install the recommended **SQLite Viewer** extension (`.vscode/extensions.json`)

Tables: `recordings`, `frames`.

### Process a video file via API in Docker

Add a bind mount under `docker-compose.yml` (e.g. `./samples:/app/samples:ro`), then call the API from your host:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/recordings/start \
  -H "Content-Type: application/json" \
  -d '{"title":"Test","source_video_path":"/app/samples/video.mp4","frame_interval_seconds":1}'
```

### Docker environment variables

| Variable | Server | Client | Description |
|----------|--------|--------|-------------|
| `APP_ROLE` | `server` | `client` | Which workers start |
| `DATABASE_URL` | `jarvis.db` | `client-buffer.db` | Database path |
| `JARVIS_SERVER_URL` | — | server URL | Where client uploads data |
| `SYNC_API_KEY` | required | required | Shared auth for sync API |
| `SCREENPIPE_ENABLED` | `false` | `true` | Screen capture |
| `CHROMA_ENABLED` | `true` | `false` | Vector search on server |
| `WHATSAPP_ENABLED` | `true` | `false` | WhatsApp on server only |

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

On startup, **`screenpipe record`** runs and **paddle_ocr** processes new frames. Set `AUTO_START_SERVICES=false` to disable.

Install **Node.js** for Screenpipe CLI and OCR runtime (`paddlepaddle`) separately:

```bash
pip install paddleocr
pip install paddlepaddle
```

Also make sure `ffmpeg` is available on your machine:

```bash
ffmpeg -version
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCREENPIPE_ENABLED` | `true` | Use Screenpipe CLI + API (not ffmpeg interval) |
| `SCREENPIPE_CLI_COMMAND` | `npx -y screenpipe@latest record` | Command to start recording |
| `SCREENPIPE_API_URL` | `http://127.0.0.1:3030` | Screenpipe local API |
| `SCREENPIPE_START_CLI` | `true` | Launch `screenpipe record` from this app |
| `SCREENPIPE_POLL_INTERVAL_SECONDS` | `2.0` | Poll API for new frames |
| `AUTO_START_SERVICES` | `true` | Start sync + OCR workers with the server |
| `OCR_POLL_INTERVAL_SECONDS` | `0.5` | OCR worker poll interval |
| `DATABASE_URL` | `data/jarvis.db` (via config) | SQLite connection string |
| `SCREENPIPE_ENABLED=false` | — | Falls back to ffmpeg interval capture |

## API endpoints

- `GET /health`
- `GET /api/v1/services/status`
- `POST /api/v1/sync/*` (server only — desktop upload endpoints)
- `GET /api/v1/recordings`
- `POST /api/v1/recordings`
- `POST /api/v1/recordings/start`
- `GET /api/v1/recordings/{id}`
- `GET /api/v1/recordings/{id}/frames`
- `PATCH /api/v1/recordings/{id}`
- `DELETE /api/v1/recordings/{id}`

## Live capture (desktop client)

When the **desktop client** starts, **`screenpipe record`** runs and new frames (on screen change) are synced locally, then uploaded to the server.

`media/recording_<id>/frames/frame_000001.jpg` on the desktop. OCR text and activity chunks sync to the central server database.

Check desktop client health:

```bash
curl http://127.0.0.1:8000/api/v1/services/status
```

## One-shot OCR pipeline (API)

For an existing video file or a full capture command:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/recordings/start \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Daily capture OCR",
    "source": "screen",
    "source_video_path": "/absolute/path/to/capture.mp4",
    "frame_interval_seconds": 1
  }'
```

Video lands in `screenpipe/`, frames in `frames/`, OCR text in `ocr/` and in the database.

Example `capture_command`:

```json
{
  "title": "Live capture",
  "capture_command": "ffmpeg -y -f x11grab -video_size 1920x1080 -i :0.0 -t 10 {output}"
}
```

## Scalability notes

- Each service lives in its own folder and can be moved to a separate process later.
- The next scale step is running `ScreenpipeService` and `PaddleOcrService` as standalone workers (Celery, RQ, or separate containers) without changing the API contract.
