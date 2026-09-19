# TypeScale

A local playable multiplayer typing race and the application foundation for a cloud-native Azure/AKS engineering project.

## What works now

- Guest nickname and automatic matchmaking
- Two to four players per room
- Shared passage and server-controlled three-second countdown
- Server-validated typing progress, WPM, accuracy, finish order, and standings
- Live WebSocket progress and disconnect handling
- Responsive browser UI and replay flow
- Health endpoint and automated real two-client WebSocket race test

This first milestone intentionally keeps state in one Python process. Redis, Java, PostgreSQL, containers, Kubernetes, and Azure belong to later milestones.

## Run locally

Requires Python 3.9+.

```bash
cd /Users/praneethkoushik/Dev/typescale-app
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000** in two browser windows, enter different nicknames, and race.

## Test

```bash
source .venv/bin/activate
pytest -q
```

The integration test starts a real local server, connects two WebSocket clients, verifies that they share a room and paragraph, rejects invalid typing, completes both racers, and checks final standings.

## Ultimate project — 10 sub-tasks

1. **Foundation and local MVP** — Build the browser UI and FastAPI WebSocket race; validate a complete two-player match locally. **Current milestone.**
2. **Java/Python service split** — Add the Spring Boot matchmaking/results API while Python owns real-time gameplay.
3. **Distributed state and persistence** — Move room state and cross-pod events to Redis; persist idempotent race results and leaderboards in PostgreSQL/Flyway.
4. **Containers and local composition** — Build non-root images, health checks, retries, and a reproducible Docker Compose stack.
5. **Local Kubernetes** — Deploy to a lean kind cluster; practise Services, Ingress, probes, resources, scaling, and pod-failure behaviour.
6. **Azure infrastructure with Terraform** — Provision remote state, networking, ACR, AKS, identities, Key Vault, PostgreSQL, and monitoring with dev/prod configuration.
7. **CI, security scanning, and ACR** — Test, build, scan with Trivy/dependency tooling, authenticate through OIDC, publish immutable images, and protect merges/environments.
8. **Helm, Kustomize, and Argo CD GitOps** — Package services, model environment overlays, bootstrap Argo CD, promote image digests, and prove rollback.
9. **Ingress, identity, and secrets** — Add TLS/WebSocket routing, workload identity, Key Vault CSI, least-privilege RBAC, network policies, and private data services.
10. **Observability, autoscaling, reliability, and evidence** — Instrument Prometheus metrics; add Grafana, KEDA, HPA/node scaling, realistic load tests, failure experiments, alerting, runbooks, incident reports, cost evidence, hardening, and final documentation.

## Current architecture

```text
Browser A ─┐
           ├── HTTP + WebSocket ── FastAPI (in-memory rooms)
Browser B ─┘                         ├── / health
                                     └── /static frontend
```

## Protocol summary

The client first sends `{"type":"join","nickname":"..."}`. During a race it sends only typed text: `{"type":"progress","text":"..."}`. The server derives validated progress, timing, WPM, accuracy, and placement; client-claimed results are never accepted.
