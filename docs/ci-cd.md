# CI/CD

This repository uses GitHub Actions for backend validation and deployment.

## Workflows

### Backend CI

File: `.github/workflows/ci.yml`

Triggers:

- `pull_request` targeting `main`
- `push` to `main`, `fix/**`, `feat/**`, and `ref/**`

Stages:

- Install Python 3.12 dependencies with `uv sync`
- Run Django system checks
- Run backend tests:
  - `research.tests`
  - `llm_manager.tests`
  - `analytics.tests`
- Build the Docker image without pushing it

The CI job starts PostgreSQL and Redis service containers so future
database-backed or cache-backed tests can run without redesigning the workflow.

### Backend CD

File: `.github/workflows/deploy.yml`

Trigger:

- Automatic `workflow_run` after `Backend CI` succeeds on `main`.
- Manual `workflow_dispatch`

Inputs:

- `ref`: Git ref to deploy. Defaults to `main`.
- `environment`: GitHub environment. `staging` or `production`.
- `publish_image`: Build and push `ghcr.io/<owner>/8feet-backend`.
- `deploy_ssh`: Deploy to the configured SSH host.

Automatic deployments always deploy `main` to the `production` environment over
SSH and skip GHCR publishing. Use the manual trigger when deploying another ref,
deploying to `staging`, or publishing a GHCR image.

Production deployments should be protected with GitHub Environment approvals.

## Required Secrets

Configure these in the target GitHub Environment, not as committed files.

| Secret | Required | Description |
| --- | --- | --- |
| `DEPLOY_SSH_HOST` | yes for SSH deploy | Server hostname or IP |
| `DEPLOY_SSH_USER` | yes for SSH deploy | SSH user |
| `DEPLOY_SSH_KEY` | yes for SSH deploy | Private key with access to the server |
| `DEPLOY_SSH_PORT` | no | SSH port, defaults to `22` |
| `DEPLOY_APP_DIR` | yes for SSH deploy | Existing backend repo path on the server |

The remote host is expected to already contain a checked-out copy of this
repository, Docker, and Docker Compose. Runtime secrets such as
`DJANGO_SECRET_KEY`, database passwords, Redis credentials, and S3 credentials
should live in the server-side `.env` file used by `docker-compose.yml`.

The deployed Docker Compose stack runs three application services from the same
backend image:

- `backend`: Daphne/Django HTTP and WebSocket server.
- `celery-worker`: asynchronous jobs such as report exports and research tasks.
- `celery-beat`: scheduler for periodic jobs, including daily/weekly alert
  reminders. Keep this service running in production; otherwise alert updates can
  still enqueue immediate work, but configured reminder times will not be scanned.

## Deployment Flow

Automatic production deployment:

1. Push or merge to `main`.
2. Wait for `Backend CI` to complete successfully.
3. `Backend CD` starts automatically and deploys `main` to `production`.

Manual deployment:

1. Open GitHub Actions.
2. Select `Backend CD`.
3. Run workflow.
4. Choose `staging` or `production`.
5. Set `ref` to the branch, tag, or commit you want to deploy.
6. Keep `publish_image` enabled when you want a GHCR image for the run.
7. Keep `deploy_ssh` enabled when you want the server updated.

The SSH deploy step runs:

```bash
git fetch origin
git worktree add --force --detach <temporary-worktree> <ref>
BACKEND_IMAGE=<image-ref> docker compose pull backend celery-worker celery-beat
BACKEND_IMAGE=<image-ref> docker compose up -d --remove-orphans
docker compose ps
curl -fsS -H 'Host: 8feet.meteor041.com' http://127.0.0.1:48881/readyz
```

## Rollback

Rollback is manual and explicit:

1. Find the last known-good commit or tag.
2. Run `Backend CD` again with `ref` set to that commit or tag.
3. Confirm `docker compose ps` is healthy.
4. Confirm `http://127.0.0.1:8000/readyz` returns success on the server.

If the server cannot be reached by GitHub Actions, SSH into the server and run:

```bash
cd <DEPLOY_APP_DIR>
git fetch origin
git checkout <known-good-ref>
docker compose pull backend celery-worker celery-beat
docker compose up -d --remove-orphans
curl -fsS -H 'Host: 8feet.meteor041.com' http://127.0.0.1:48881/readyz
```
