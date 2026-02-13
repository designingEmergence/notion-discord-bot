# Server Secrets Setup (Shared Server)

Use this runbook to keep API keys private when multiple people can access the same server.

## 1) Create a dedicated runtime user

```bash
sudo useradd -r -s /usr/sbin/nologin botservice
```

## 2) Store `.env` outside the repo with strict permissions

```bash
sudo mkdir -p /opt/notion-bot/secrets
sudo cp /path/to/your/.env /opt/notion-bot/secrets/.env
sudo chown botservice:botservice /opt/notion-bot/secrets/.env
sudo chmod 600 /opt/notion-bot/secrets/.env
```

Only `botservice` and `root` can read this file.

## 3) Run Docker Compose as the service user

```bash
sudo -u botservice docker compose --env-file /opt/notion-bot/secrets/.env up -d
```

This keeps secrets out of the image and out of git.

## 4) Restrict Docker group access

Users in the `docker` group can inspect containers and potentially view environment variables.

- Keep `docker` group membership minimal
- Avoid adding shared users to `docker`

## 5) Optional hardening

For stronger secrecy, move to Docker Swarm secrets or Vault so secrets are mounted as files at runtime instead of env variables.
