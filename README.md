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

## Same data folders (Docker bind mounts)

Docker writes to your project folders on the host:

| Path | Contents |
|------|----------|
| `data/jarvis.db` | SQLite — open in VS Code |
| `media/recording_<id>/` | frames, OCR text, capture video |

```text
  Local uvicorn                    Docker container
        |                                |
        v                                v
   data/jarvis.db      <=========>  /app/data/jarvis.db
   media/               <=========>  /app/media/
```

Optional: `cp .env.example .env` (for Docker `AUTO_START_SERVICES`, etc.). Do **not** set `DATABASE_URL` in `.env` for local uvicorn.

**Run one server at a time** (same port `8000` and one SQLite writer). Stop Docker before local, or the other way around:

```bash
docker compose down    # stop Docker
# or
# Ctrl+C on uvicorn
```

## Screenpipe CLI (default)

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

### Docker only (recommended — works on any Linux machine)

Everything runs **inside Docker**. No host Python, Node, screenpipe, or uvicorn required.

| Component | Where it runs |
|-----------|----------------|
| FastAPI | container |
| `screenpipe record --audio-all` | container |
| Paddle OCR | container |
| Meeting audio | container → host PulseAudio socket |
| `data/` + `media/` | bind-mounted to project folder |

**New machine — two commands:**

```bash
./scripts/docker-setup.sh   # once: creates .env, builds image, gets API token
./scripts/docker-up.sh      # start everything
```

**Daily use:**

```bash
./scripts/docker-up.sh      # start
./scripts/docker-down.sh    # stop
./scripts/docker-doctor.sh  # check display, audio, API health
```

- API: http://127.0.0.1:8000  
- Status: http://127.0.0.1:8000/api/v1/services/status  

Frames sync when you **use the desktop** (Screenpipe is event-driven). First start can take 2–3 minutes.

**Host requirements (any Linux desktop):**

| Required | Why |
|----------|-----|
| Docker + Docker Compose | runs the stack |
| Logged-in GUI session (X11/Wayland) | screen capture |
| PipeWire or PulseAudio (default on Ubuntu) | meeting audio transcripts |

**Not required on host:** Python, Node, screenpipe, uvicorn, venv.

**API token:** `docker-setup.sh` tries to write `SCREENPIPE_API_TOKEN` to `.env` automatically. Or run:

```bash
docker compose exec jarvis-bot screenpipe auth token
```

**Meeting audio in Docker:** the container mounts your PulseAudio socket (`/run/user/$UID/pulse`) and `/dev/snd`. Check:

```bash
curl http://127.0.0.1:3030/health   # audio_status should be "ok"
```

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

| Variable | Default in compose | Description |
|----------|-------------------|-------------|
| `PORT` | `8000` | Host port mapped to the container |
| `DATABASE_URL` | `sqlite:////app/data/jarvis.db` | SQLite path (default: `data/jarvis.db` locally) |
| `AUTO_START_SERVICES` | `true` | Start screenpipe sync + OCR on boot |
| `SCREENPIPE_CLI_COMMAND` | `npx -y screenpipe@latest record` | Screenpipe record command |
| `SCREENPIPE_API_URL` | `http://host.docker.internal:3030` | Screenpipe REST API |
| `SCREENPIPE_START_CLI` | `true` | Start `screenpipe record` inside container |
| `SCREENPIPE_POLL_INTERVAL_SECONDS` | `2.0` | How often to check for new frames |
| `OCR_POLL_INTERVAL_SECONDS` | `0.5` | OCR worker poll interval |

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
- `GET /api/v1/recordings`
- `POST /api/v1/recordings`
- `POST /api/v1/recordings/start`
- `GET /api/v1/recordings/{id}`
- `GET /api/v1/recordings/{id}/frames`
- `PATCH /api/v1/recordings/{id}`
- `DELETE /api/v1/recordings/{id}`

## Live capture (server start)

When the server starts, **`screenpipe record`** runs and new frames (on screen change) are synced to:

`media/recording_<id>/frames/frame_000001.jpg`

The paddle_ocr worker picks up frames with `ocr_status=queued`, runs PaddleOCR, sets `ocr_status=done`, and saves text to:

`media/recording_<id>/ocr/frame_000001.txt`

Check service health:

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
