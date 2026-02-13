# Notion Discord RAG (Self-Hosted)

This repository runs a Discord + Notion RAG bot using:

- Python bot service
- PostgreSQL (for bot config state)
- ChromaDB persisted on disk (for embeddings/doc vectors)
- Docker Compose for deployment and restart management

## Architecture

- `bot` container: runs `python src/main.py`
- `postgres` container: stores `bot_config` table
- `chroma_db/`: persisted locally on host via bind mount
- health endpoint: `http://127.0.0.1:8080/`

## Prerequisites

- Linux server (Ubuntu/Debian recommended)
- Docker Engine + Docker Compose plugin
- A Discord bot token
- A Notion integration token + resource ID
- OpenAI API key

## 1) Server setup

### Install Docker (Ubuntu/Debian)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 2) Clone and configure

```bash
git clone https://github.com/designingEmergence/notion-discord-bot.git
cd notion-discord-bot
cp .env.example .env
```

Fill `.env` with real values:

- `DISCORD_TOKEN`
- `NOTION_TOKEN`
- `NOTION_RESOURCE_ID`
- `OPENAI_API_KEY`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `DATABASE_URL` (format: `postgresql://user:password@postgres:5432/dbname`)
- `ADMIN_IDS`

## 3) Secure secrets on shared servers

Follow the dedicated runbook in [docs/SERVER_SECRETS_SETUP.md](docs/SERVER_SECRETS_SETUP.md).

Minimum recommended setup:

```bash
sudo useradd -r -s /usr/sbin/nologin botservice
sudo mkdir -p /opt/notion-bot/secrets
sudo cp .env /opt/notion-bot/secrets/.env
sudo chown botservice:botservice /opt/notion-bot/secrets/.env
sudo chmod 600 /opt/notion-bot/secrets/.env
```

## 4) Start the stack

Standard local deployment:

```bash
docker compose up -d --build
```

Shared server deployment (recommended with protected env file):

```bash
sudo -u botservice docker compose --env-file /opt/notion-bot/secrets/.env up -d --build
```

## 5) Verify and operate

```bash
docker compose ps
docker compose logs -f bot
docker compose logs -f postgres
curl http://127.0.0.1:8080/
```

Common operations:

```bash
docker compose restart bot
docker compose pull
docker compose up -d
docker compose down
```

## 6) Backups and maintenance

### PostgreSQL backup

```bash
mkdir -p backups
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backups/postgres_$(date +%F).sql
```

### PostgreSQL restore

```bash
cat backups/postgres_YYYY-MM-DD.sql | docker compose exec -T postgres psql -U "$POSTGRES_USER" "$POSTGRES_DB"
```

### ChromaDB backup

```bash
tar -czf backups/chroma_db_$(date +%F).tar.gz chroma_db
```

### Recommended maintenance cadence

- Daily: check `docker compose ps` and bot logs
- Weekly: backup Postgres + `chroma_db`
- Monthly: pull updated base images and redeploy

## 7) Migration from Railway

1. Export Railway Postgres `bot_config` table with `pg_dump`
2. Deploy this self-hosted stack
3. Import dump into local Postgres
4. Validate bot commands and sync behaviour
5. Decommission Railway resources

## License

MIT. See [LICENSE](LICENSE).