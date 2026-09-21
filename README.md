# TypeScale

TypeScale is a local multiplayer typing race and a distributed-application foundation for an Azure/AKS engineering project.

**Current milestone: v0.5 — local Kubernetes deployment and failure testing.**

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
- Hardened, non-root Python 3.13 Alpine image with build tooling removed
- Blocking Trivy scan for HIGH and CRITICAL OS/library vulnerabilities
- Docker Compose stack with private Redis and two FastAPI replicas
- Nginx HTTP/WebSocket gateway with round-robin upstream routing
- Responsive browser UI, disconnect handling, and replay flow
- Automated same-process and cross-process WebSocket race tests
- OrbStack Kubernetes deployment with Kustomize, probes, resource limits, NetworkPolicies, two application replicas, and a private Redis service

## Current architecture

```text
Browsers ── HTTP/WebSocket ── Nginx gateway :8080
                                  ├── FastAPI app-a :8000 ─┐
                                  └── FastAPI app-b :8000 ─┼── Redis 7
                                                          └── state + Pub/Sub
```

Nginx distributes new HTTP and WebSocket connections between two independent FastAPI containers. Each application container owns only its local sockets. Redis is private to the Compose network and remains authoritative for matchmaking, race state, atomic finish order, and cross-replica events.

## Requirements

- Python 3.10+ for local development (Python 3.13 recommended)
- Docker-compatible runtime such as OrbStack or Docker Desktop
- Node.js only for the frontend regression test

The pinned FastAPI release requires Python 3.10 or newer. CI and the production image use Python 3.13.

## Setup

```bash
cd /Users/praneethkoushik/Dev/typescale-app
python3.13 -m venv .venv
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

## Run on OrbStack Kubernetes

Build the local development image, start OrbStack Kubernetes, and apply the complete stack:

```bash
docker build --pull -t typescale:v0.5.5-dev .
orb start k8s
kubectl config use-context orbstack
kubectl apply -k deploy/kubernetes
kubectl rollout status deployment/redis -n typescale
kubectl rollout status deployment/typescale -n typescale
```

Open http://typescale-public.typescale.svc.cluster.local. This hostname and the OrbStack LoadBalancer IP are local development endpoints, not internet-accessible production endpoints. OrbStack shares locally built images with its Kubernetes cluster; an AKS deployment will use an ACR image reference instead.

Inspect the deployment:

```bash
kubectl get all -n typescale
kubectl get endpointslice -n typescale
kubectl get networkpolicy -n typescale
```

Validate manifests without changing the cluster:

```bash
kubectl kustomize deploy/kubernetes
kubectl apply --dry-run=server -k deploy/kubernetes
```

Kustomize renders and applies the desired resources, but it is not a continuously reconciling GitOps controller. Argo CD will provide drift correction in a later milestone.

### Health endpoints

| Endpoint | Purpose | Redis required |
|---|---|---|
| `/health/live` | Confirms the Python process can serve requests | No |
| `/health/ready` | Confirms the replica can serve Redis-backed gameplay | Yes |
| `/health/startup` | Confirms application initialization completed | No |
| `/health` | Backward-compatible readiness check | Yes |

Kubernetes uses separate startup, readiness, and liveness probes. A temporary Redis outage removes application pods from Service endpoints without restarting healthy Python processes.

### Current reliability boundaries

- Redis is a single, ephemeral development pod. Its replacement is automatic, but active room and matchmaking state is lost after a Redis restart.
- Replacing an application pod restores replica capacity and Service health; it does not preserve WebSocket connections or automatically rejoin affected players to an active room.
- The LoadBalancer is local to OrbStack. Public AKS ingress, TLS, managed identities, and cloud network controls are future work.
- NetworkPolicies restrict Redis ingress to TypeScale application pods and expose only the application container port. Enforcement in AKS will also depend on the selected network-policy-capable dataplane.

### Reproduce the failure experiments

Simulate a Redis outage and recovery:

```bash
kubectl scale deployment/redis -n typescale --replicas=0
kubectl get pods -n typescale

kubectl apply -f deploy/kubernetes/redis-deployment.yaml
kubectl rollout status deployment/redis -n typescale
kubectl wait --for=condition=Ready pod \
  -n typescale \
  -l app.kubernetes.io/component=application \
  --timeout=120s
```

Observed result: application liveness and startup remained healthy, readiness became false, both application containers stayed running with zero restarts, readiness recovered after Redis returned, and a new cross-replica race completed. Data written before replacement was absent afterward because Redis persistence is deliberately disabled.

To distinguish pod self-healing from session continuity, start a two-player race and delete an application pod:

```bash
pod=$(kubectl get pods \
  -n typescale \
  -l app.kubernetes.io/component=application \
  -o jsonpath='{.items[0].metadata.name}')

kubectl delete pod -n typescale "$pod" --wait=false
kubectl rollout status deployment/typescale -n typescale
```

Observed result: Kubernetes restored two Ready replicas and a healthy public endpoint, but WebSocket clients attached to the deleted pod disconnected and did not automatically rejoin the active room. This is documented as a limitation rather than described as uninterrupted recovery.

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
- Separate liveness, readiness, and startup endpoint behavior
- Readiness degradation without liveness failure during Redis errors
- Redis startup retry and Pub/Sub resubscription after connection loss

## Implemented Redis milestone

1. Created `feat/redis-distributed-state` from a passing `main` baseline.
2. Started and verified localhost-only Redis 7.
3. Added and pinned the asynchronous Redis client.
4. Separated pure typing calculations, distributed game coordination, and Redis storage.
5. Implemented atomic matchmaking, authoritative shared state, countdown deadlines, finish ordering, and TTLs.
6. Implemented Redis Pub/Sub delivery to process-local WebSocket clients.
7. Ran two independent FastAPI processes and completed a verified cross-process race.

## Next milestones

1. **Helm packaging** — reproduce the tested Kustomize deployment in a clean namespace with environment-specific values.
2. **Azure with Terraform** — remote state, networking, ACR, AKS, identities, Key Vault, cost controls, and teardown procedures.
3. **GitOps** — ACR publishing, Helm configuration, and Argo CD reconciliation.
4. **Observability and scaling** — Prometheus, Grafana, KEDA, load tests, alerts, and controlled failures.
5. **Optional persistence** — PostgreSQL results/leaderboard only if later product requirements justify it.
