# TypeScale

TypeScale is a local multiplayer typing race and a distributed-application foundation for an Azure/AKS engineering project.

**Current milestone: v0.3 — containerized distributed multiplayer.**

## What works now

- Guest nickname validation and automatic matchmaking
- Two to four players per room
- Shared paragraph and one authoritative three-second countdown
- Character-specific Monkeytype-style correctness feedback
- Server-calculated progress, WPM, accuracy, finish order, and standings
- Redis-backed room, player, progress, countdown, and completion state
- Atomic Lua operations for matchmaking slots and finish ordering
- Redis Pub/Sub fan-out between independent FastAPI processes
- Active-room and completed-room TTL cleanup
- Non-root Python 3.13 application image with a Docker health check
- Docker Compose stack with private Redis and two FastAPI replicas
- Nginx HTTP/WebSocket gateway with round-robin upstream routing
- Responsive browser UI, disconnect handling, and replay flow
- Automated same-process and cross-process WebSocket race tests

## Current architecture

```text
Browsers ── HTTP/WebSocket ── Nginx gateway :8080
                                  ├── FastAPI app-a :8000 ─┐
                                  └── FastAPI app-b :8000 ─┼── Redis 7
                                                          └── state + Pub/Sub
```

Nginx distributes new HTTP and WebSocket connections between two independent FastAPI containers. Each application container owns only its local sockets. Redis is private to the Compose network and remains authoritative for matchmaking, race state, atomic finish order, and cross-replica events.

## Requirements

- Python 3.9+ for the current local environment
- Docker-compatible runtime such as OrbStack or Docker Desktop
- Node.js only for the frontend regression test

Python 3.9 is supported by the current code but is end-of-life upstream. Use Python 3.12 or 3.13 for the production container milestone.

## Setup

```bash
cd /Users/praneethkoushik/Dev/typescale-app
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Start disposable local Redis, bound only to localhost:

```bash
docker run -d \
  --name typescale-redis \
  -p 127.0.0.1:6379:6379 \
  redis:7-alpine

docker exec typescale-redis redis-cli ping
```

Expected response: `PONG`.

If the container already exists but is stopped:

```bash
docker start typescale-redis
```

## Run one application process

```bash
REDIS_URL=redis://127.0.0.1:6379/0 \
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 in two browser windows.

## Prove distributed multiplayer

Start two independent processes in separate terminals:

```bash
REDIS_URL=redis://127.0.0.1:6379/0 \
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
REDIS_URL=redis://127.0.0.1:6379/0 \
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open http://127.0.0.1:8000 and http://127.0.0.1:8001. Players connected to different processes should join the same room, see the same countdown and progress, and receive identical standings.

## Run with Docker Compose

Build and start Redis, two FastAPI replicas, and the Nginx gateway:

```bash
docker compose up -d --build --wait
```

Open http://127.0.0.1:8080 in two browser windows. Nginx forwards both normal HTTP traffic and WebSocket connections to the application replicas. The direct replica endpoints at ports `8000` and `8001` are available for local verification only.

Inspect the stack and logs:

```bash
docker compose ps
docker compose logs
docker compose logs gateway
```

Stop and remove the containers and private Compose network:

```bash
docker compose down
```

The locally built image remains available after shutdown. Compose does not publish Redis to the host; application containers reach it internally at `redis://redis:6379/0`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `TYPESCALE_NAMESPACE` | `typescale` | Key prefix used to isolate an environment |

Do not commit production Redis credentials. Use environment configuration or a secret manager.

## Redis data model

| Key/channel | Purpose |
|---|---|
| `typescale:waiting` | Current room accepting matchmaking joins |
| `typescale:room:{id}` | Room status, paragraph, player count, countdown deadline, start time, and finish counter |
| `typescale:room:{id}:players` | Authoritative JSON player states |
| `typescale:room:{id}:events` | Pub/Sub channel for room events |
| `typescale:paragraph:index` | Shared paragraph rotation counter |

Active rooms expire after one hour. Completed rooms are retained for 15 minutes and then expire automatically.

## Test

Redis must be running. Tests use Redis database 15 and isolated key namespaces.

```bash
source .venv/bin/activate
pytest -q
```

The suite verifies:

- Health and frontend delivery
- Character-specific typing feedback
- A complete same-process WebSocket race
- A complete race across two independent FastAPI processes
- Shared cross-process progress and identical standings
- Atomic concurrent matchmaking with no room exceeding four players
- Completed-room TTL cleanup configuration

## Implemented Redis milestone

1. Created `feat/redis-distributed-state` from a passing `main` baseline.
2. Started and verified localhost-only Redis 7.
3. Added and pinned the asynchronous Redis client.
4. Separated pure typing calculations, distributed game coordination, and Redis storage.
5. Implemented atomic matchmaking, authoritative shared state, countdown deadlines, finish ordering, and TTLs.
6. Implemented Redis Pub/Sub delivery to process-local WebSocket clients.
7. Ran two independent FastAPI processes and completed a verified cross-process race.

## Next milestones

1. **GitHub Actions CI** — tests, image build, dependency checks, and Trivy scanning on pull requests.
2. **Local Kubernetes with kind** — manifests, probes, resources, replicas, failures, then Helm.
3. **Azure with Terraform** — remote state, networking, ACR, AKS, identities, and Key Vault.
4. **GitOps** — ACR publishing, Helm/Kustomize configuration, and Argo CD reconciliation.
5. **Observability and scaling** — Prometheus, Grafana, KEDA, load tests, alerts, and controlled failures.
6. **Optional persistence** — PostgreSQL results/leaderboard and an optional separate Java service only if justified.
