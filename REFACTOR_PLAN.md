# Refactor Plan: Self-Hosted Local Server Deployment

## Current State Summary

| Component | Current Implementation |
|---|---|
| **Deployment** | Railway (via `railway.taml`, `Procfile`, `Dockerfile`) |
| **Config DB** | Railway-managed PostgreSQL (`asyncpg`, `DATABASE_URL` / `DATABASE_PUBLIC_URL`) |
| **Vector DB** | ChromaDB (file-based, persisted to `chroma_db/`) |
| **Process Manager** | Supervisord (single process: the Discord bot) |
| **Health Check** | aiohttp web server on `PORT` (default 8080) |
| **Secrets** | Railway environment variables injected at deploy time |

### Files to Remove
- `railway.taml` — Railway-specific deploy config
- `Procfile` — Railway/Heroku process definition (replaced by Docker Compose)

### Files to Modify
- `Dockerfile` — Simplify; remove Railway env var passthrough pattern
- `supervisord.conf` — Simplify env var handling
- `src/config.py` — Remove `DATABASE_PUBLIC_URL` / Railway dual-URL logic
- `src/main.py` — Remove `--use-public-db` flag, Railway `PORT` convention
- `src/bot/bot.py` — Remove `use_public_db` parameter threading
- `requirements.txt` — Add `psycopg2-binary` or keep `asyncpg` (no change needed for asyncpg)
- `setup.py` — Update dependencies list
- `README.md` — Rewrite deployment instructions

### Files to Create
- `docker-compose.yml` — Orchestrate bot + PostgreSQL containers
- `.env.example` — Create or update template for required environment variables
- `init-db/init.sql` — PostgreSQL initialisation script (create DB + table)

---

## 1. Securing Secrets on a Shared Server

**Problem:** Multiple people can SSH into / access the server. Raw `.env` files or environment variables visible via `/proc` would expose API keys.

### Approach: Docker Secrets + Restricted File Permissions (Layered)

| Layer | What It Does |
|---|---|
| **Dedicated service user** | Create a `botservice` Linux user. Only this user (and root) can read the secrets file. Other users on the server cannot access it. |
| **File permissions on `.env`** | `chmod 600 .env` + `chown botservice:botservice .env`. No other user can read the file. |
| **Docker Compose `env_file`** | The `.env` is only mounted into the bot container at runtime — it is never baked into the image or visible to other containers/users. |
| **Docker secrets (optional升级)** | For extra hardiness, use Docker Swarm mode secrets (`docker secret create`) which stores secrets encrypted at rest and mounts them as in-memory files inside the container at `/run/secrets/<name>`. This avoids secrets appearing in `docker inspect` or process environment tables. |
| **`.dockerignore` + `.gitignore`** | Ensure `.env` is never committed to git or included in the Docker image build context. |

### Recommended Setup

```
# On the server, as root:
useradd -r -s /usr/sbin/nologin botservice
# Place .env in a protected directory
mkdir -p /opt/notion-bot/secrets
cp .env /opt/notion-bot/secrets/.env
chown botservice:botservice /opt/notion-bot/secrets/.env
chmod 600 /opt/notion-bot/secrets/.env

# docker-compose.yml references this file via env_file
# Only botservice (or root) can read it
# Other server users get "Permission denied"
```

#### Why Not Just Environment Variables?
- Env vars set via `export` in a shell are visible to anyone who can read `/proc/<pid>/environ`
- Docker Compose `env_file` injects them only into the container's process namespace, but `docker inspect` can still reveal them to users with Docker access
- For maximum security, Docker Swarm secrets or a vault (e.g., HashiCorp Vault) can be added later — but file permissions + dedicated user is sufficient for most self-hosted scenarios

---

## 2. Docker Compose Architecture

Replace Railway with a `docker-compose.yml` that runs two services:

```
┌─────────────────────────────────────────────┐
│              Docker Compose                  │
│                                              │
│  ┌────────────────────┐  ┌───────────────┐  │
│  │   notion-bot        │  │   postgres    │  │
│  │   (Python app)      │──│   (DB)        │  │
│  │   Port 8080 (health)│  │   Port 5432   │  │
│  └────────────────────┘  └───────────────┘  │
│         │                       │            │
│    volumes:                volumes:          │
│    ./chroma_db             pgdata            │
│                           ./init-db          │
└─────────────────────────────────────────────┘
```

### `docker-compose.yml` Plan

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./init-db:/docker-entrypoint-initdb.d  # auto-run init.sql
    ports:
      - "127.0.0.1:5432:5432"  # bind to localhost only — no external access
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 5

  bot:
    build: .
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./chroma_db:/app/chroma_db  # persist vector DB
    ports:
      - "127.0.0.1:8080:8080"  # health check, localhost only

volumes:
  pgdata:
```

### Key Design Decisions
- **PostgreSQL binds to `127.0.0.1` only** — not accessible from outside the server
- **Bot container gets `.env` via `env_file`** — secrets never baked into the image
- **`chroma_db` is volume-mounted** — persists across container restarts
- **`depends_on` with health check** — bot waits for Postgres to be ready before starting

---

## 3. Database URL Refactor

### Current (`config.py`)
```python
# Two URL modes: DATABASE_URL (private Railway network) vs DATABASE_PUBLIC_URL
if use_public_db:
    self.db_url = os.getenv("DATABASE_PUBLIC_URL")
else:
    self.db_url = os.getenv("DATABASE_URL")
```

### New
- Single `DATABASE_URL` env var pointing to the Compose Postgres service
- Format: `postgresql://user:password@postgres:5432/dbname` (uses Docker DNS `postgres` as hostname)
- Remove `use_public_db` flag everywhere it's threaded through

### Changes Required

| File | Change |
|---|---|
| `src/config.py` | Remove `use_public_db` constructor param. Use only `DATABASE_URL`. Remove `DATABASE_PUBLIC_URL` logic. |
| `src/main.py` | Remove `--use-public-db` argparse flag. Remove `use_public_db` kwarg to `NotionBot()`. |
| `src/bot/bot.py` | Remove `use_public_db` param from `NotionBot.__init__()`. Pass no flag to `ConfigManager()`. |

---

## 4. Dockerfile Simplification

### Current Issues
- Hardcodes Railway env vars with `ENV OPENAI_API_KEY=${OPENAI_API_KEY}` lines (these are build-time and don't work as intended)
- Uses supervisord for a single process (unnecessary complexity)

### New Dockerfile Plan
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bot is the only process — run directly
CMD ["python", "src/main.py"]
```

### Supervisord Decision
- **Remove supervisord** — it's managing a single process. Docker Compose `restart: unless-stopped` handles restarts.
- Remove `supervisor` from `requirements.txt`
- Delete `supervisord.conf`
- Simplify CMD to run `python src/main.py` directly

---

## 5. Main Entry Point Cleanup (`src/main.py`)

| Change | Detail |
|---|---|
| Remove `--use-public-db` argparse | No longer needed |
| Remove `argparse` import | Unless other args are added |
| Keep aiohttp health check | Useful for monitoring; hardcode port to 8080 or read from `PORT` env var |
| Add `load_dotenv()` | Uncomment/add for local dev outside Docker |
| Add DB readiness wait | Simple retry loop to wait for Postgres on startup |

---

## 6. Environment Variables

### `.env.example` (create or update)
```
# Discord
DISCORD_TOKEN=your_discord_bot_token
ADMIN_IDS=comma,separated,user,ids
STATUS_MESSAGE=optional_status_message

# Notion
NOTION_TOKEN=your_notion_integration_token
NOTION_RESOURCE_ID=your_notion_database_or_page_id

# OpenAI
OPENAI_API_KEY=your_openai_api_key

# PostgreSQL (used by docker-compose AND the bot)
POSTGRES_USER=notionbot
POSTGRES_PASSWORD=a_strong_password_here
POSTGRES_DB=notionbot

# Constructed DB URL (bot reads this)
DATABASE_URL=postgresql://notionbot:a_strong_password_here@postgres:5432/notionbot

# Bot settings
COLLECTION_NAME=notion_docs
PORT=8080
```

### Variables Removed
- `DATABASE_PUBLIC_URL` — no longer needed without Railway's split networking

---

## 7. Database Initialisation

### `init-db/init.sql` (to create)
```sql
-- This runs automatically on first Postgres container start
CREATE TABLE IF NOT EXISTS bot_config (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL
);
```

This removes the need for `config.py`'s `init_db()` to create the table at runtime (though keeping it as a safety net is fine).

---

## 8. Git & Security Hygiene

### `.gitignore` additions
```
.env
chroma_db/
*.log
__pycache__/
```

### `.dockerignore` (create or update)
```
.env
.git
__pycache__
*.pyc
*.log
chroma_db/
*.egg-info/
```

This ensures:
- `.env` is never in the Docker build context (can't be leaked in image layers)
- `.env` is never committed to git

---

## 9. Full Task Checklist

### Phase 1: Remove Railway
- [x] Delete `railway.taml`
- [x] Delete `Procfile`

### Phase 2: Docker Compose Setup
- [x] Create `docker-compose.yml` (bot + postgres services)
- [x] Create `init-db/init.sql` (table creation)
- [x] Simplify `Dockerfile` (remove supervisord, remove ENV lines)
- [x] Delete `supervisord.conf`
- [x] Remove `supervisor` from `requirements.txt`

### Phase 3: Code Changes
- [x] Refactor `src/config.py` — remove `use_public_db`, use single `DATABASE_URL`
- [x] Refactor `src/main.py` — remove argparse `--use-public-db`, uncomment `load_dotenv()`
- [x] Refactor `src/bot/bot.py` — remove `use_public_db` parameter
- [x] Add startup DB connection retry logic (wait for postgres to be ready)

### Phase 4: Environment & Security
- [x] Create/update `.env.example` with all required variables documented
- [x] Update `.gitignore` (ensure `.env`, `chroma_db/`, logs excluded)
- [x] Create `.dockerignore`
- [x] Document server-side secrets setup (dedicated user, file permissions)

### Phase 5: Documentation
- [x] Rewrite `README.md` with self-hosted deployment instructions
- [x] Include server setup (user creation, permissions, Docker install)
- [x] Include `docker compose up -d` run instructions
- [x] Include backup and maintenance notes

### Phase 6: Testing
- [ ] Test `docker compose up` locally — verify bot starts, connects to Postgres, ChromaDB persists
- [ ] Test secrets not visible to non-root users on server
- [ ] Test container restart behaviour
- [ ] Test Notion sync and Discord command responses

---

## 10. Migration Path (Railway → Self-Hosted)

1. **Export current config from Railway Postgres** — `pg_dump` the `bot_config` table
2. **Provision server** — any Linux box with Docker + Docker Compose installed
3. **Clone repo, place `.env`** with correct values
4. **`docker compose up -d`** — brings up Postgres + bot
5. **Import config** — `pg_restore` / `psql < dump.sql` into the new Postgres
6. **Verify** — bot comes online in Discord, responds to commands
7. **Tear down Railway** — delete the Railway project

---

## Summary of Architectural Changes

```
BEFORE (Railway)                    AFTER (Self-Hosted)
─────────────────                   ───────────────────
Railway deploy                  →   docker compose up -d
Railway Postgres                →   Local Postgres container
Railway env vars                →   .env file (chmod 600, owned by botservice)
DATABASE_PUBLIC_URL             →   (removed)
supervisord (1 process)         →   Direct CMD in Dockerfile
railway.taml + Procfile         →   (deleted)
```
