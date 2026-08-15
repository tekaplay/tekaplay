# Deployment

Four postures, same application, differing only by configuration — the
portability guarantees in `docs/ARCHITECTURE.md` §11 (database is a
SQLAlchemy URL, Redis is a URL, object storage is only ever spoken to
through the S3 API, queueing is a Celery broker URL, payments and email sit
behind gateway modules) are what make that true. No application code imports
a cloud-provider SDK.

1. [Local development](#1-local-development)
2. [Free / very-low-cost first deployment](#2-free--very-low-cost-first-deployment)
3. [AWS](#3-aws)
4. [Azure](#4-azure)
5. [Environment variables](#5-environment-variables)
6. [Stripe setup](#6-stripe-setup)

---

## 1. Local development

```bash
cp .env.example .env
docker compose up --build
```

Compose starts Postgres 16, Redis 7, a one-shot `migrate` service (runs
`alembic upgrade head` and exits, gating `api`/`worker` on its success), the
API, a Celery worker, and the Next.js web app.

- API — http://localhost:8000 (OpenAPI at `/api/v1/docs`)
- Web — http://localhost:3000
- Postgres on host port **5433**, Redis on **6380** (deliberately offset so
  they don't collide with local installs)

Bootstrap an admin and seed the demo mission:

```bash
docker compose exec api python -m app.scripts.create_admin admin@example.com <password>
docker compose exec api python -m app.scripts.seed_demo
```

### Without Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Python 3.12
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
npm run dev
```

### Tests, lint, types

```bash
cd backend && pytest                  # full suite (SQLite, no infra needed)
cd backend && ruff check . && mypy app
cd frontend && npm run lint && npm run typecheck && npm test && npm run build
```

The seat-race test (`test_concurrent_assignment_of_last_seat`) is skipped on
SQLite and runs for real when `DATABASE_URL` points at PostgreSQL — see
[Licensing concurrency](#licensing-concurrency) below.

### Creating plans locally

Plans are runtime data, not configuration — Stripe price IDs live on `Plan`
rows rather than environment variables, so pricing changes never need a
redeploy. Create them through the admin API (requires `commerce.manage`):

```bash
# Individual monthly plan
curl -X POST http://localhost:8000/api/v1/commerce/plans \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"code":"individual-monthly","name":"Individual","price_cents":1500,
       "interval":"month","trial_days":0,"kind":"individual",
       "stripe_price_id":"price_..."}'

# Organization seat plan (quantity = seats at checkout)
curl -X POST http://localhost:8000/api/v1/commerce/plans \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"code":"team-monthly","name":"Team","price_cents":1200,
       "interval":"month","kind":"organization","stripe_price_id":"price_..."}'
```

`kind` must be `organization` for a plan to be usable at
`POST /commerce/organizations/{id}/checkout` — individual plans are rejected
there, and vice versa.

### Testing Stripe webhooks locally

`PAYMENT_PROVIDER=fake` (the default) needs no Stripe account: the fake
gateway returns deterministic ids and accepts the literal signature
`fake-signature`, so the verification path is still exercised. To drive a
lifecycle by hand:

```bash
curl -X POST http://localhost:8000/api/v1/commerce/webhooks/stripe \
  -H 'stripe-signature: fake-signature' -H 'Content-Type: application/json' \
  -d '{"id":"evt_1","type":"customer.subscription.updated",
       "data":{"object":{"id":"sub_1","customer":"cus_fake_...","status":"active"}}}'
```

Against real Stripe, use the CLI:

```bash
stripe login
stripe listen --forward-to localhost:8000/api/v1/commerce/webhooks/stripe
# copy the printed whsec_... into STRIPE_WEBHOOK_SECRET, then:
stripe trigger customer.subscription.updated
```

### Trying the organization flow end to end

1. Register two accounts.
2. As account A: `POST /api/v1/organizations` → you become `owner`.
3. `POST /api/v1/organizations/{id}/invitations` with account B's email. With
   `EMAIL_PROVIDER=console` the invitation link is written to the API logs
   (`docker compose logs api | grep invitation_email_queued`) — the token
   itself is only logged truncated, so take the full link from the
   `email_sent_console` body or read it from the event bus in tests.
4. Open `/invite/<token>` in the web app, or `POST /api/v1/invitations/accept`.
5. As an admin, grant a license: `POST /commerce/organizations/{org}/licenses/{lic}/assign`.
6. `GET /api/v1/commerce/subscription` as account B now reports
   `{"premium": true, "source": "license"}`.

---

## 2. Free / very-low-cost first deployment

The application is two deployable processes (API, Celery worker) plus a
Next.js app, backed by Postgres and Redis. A workable $0-to-a-few-dollars
starting posture:

| Component | Service | Notes |
|---|---|---|
| Postgres | **Neon** free tier | Serverless, scale-to-zero, connection pooling |
| Redis | **Upstash** free tier | Cache, rate limiting, Celery broker |
| API + worker | **Render**, **Fly.io**, or **Railway** hobby tier | Both build from `backend/Dockerfile` |
| Web | **Vercel** hobby | Native Next.js target; `output: 'standalone'` also works in a container |
| Object storage | **Cloudflare R2** free tier | S3-compatible; only used once assets land |

> Free-tier limits and pricing change, and several of these providers have
> tightened or withdrawn free plans in the past. Treat the table as a
> starting point to verify, not a guarantee. Neon and Upstash free tiers in
> particular idle/suspend after inactivity, which shows up as a slow first
> request.

**Build and start commands** (identical everywhere, because both processes
are the same image):

```
Build:      docker build -f backend/Dockerfile backend/
Migrate:    alembic upgrade head
API start:  uvicorn app.main:app --host 0.0.0.0 --port 8000
Worker:     celery -A app.workers.celery_app.celery worker --loglevel=INFO
Web build:  npm ci && npm run build
Web start:  npm start
```

**Migrations.** Run `alembic upgrade head` as a release/pre-deploy step so
the schema is in place before new code serves traffic — the same ordering
`docker-compose.yml` enforces with its `migrate` service. Never edit a
deployed database by hand; add a migration.

**Domain and HTTPS.** All of the above terminate TLS for you on their
default domains and on custom domains via managed certificates. Set
`APP_URL` to the public web origin (it builds the links in invitation and
verification emails) and `CORS_ORIGINS` to that same origin — a stale
`CORS_ORIGINS` is the usual cause of a working API and a broken browser app.

**Logs.** structlog emits JSON to stdout, enriched with `request_id`,
`correlation_id`, and `user_id`; every one of these platforms captures
stdout. Nothing extra to configure.

**Backups.** Neon takes automatic backups with point-in-time restore
(retention depends on plan). Whatever you use, verify a restore before you
rely on it — an untested backup is a hypothesis, not a backup.

---

## 3. AWS

Simplest architecture that fits an application this size. Deliberately no
Lambda/API Gateway split (the app is a long-lived ASGI server with a Celery
worker, not a set of functions) and no Aurora Serverless (RDS is cheaper and
sufficient until load justifies otherwise).

```
Route 53 → ACM cert → ALB → ECS Fargate service: api   (backend/Dockerfile)
                             ECS Fargate service: worker (same image, celery command)
                          → ECS one-off task:     migrate (same image, alembic upgrade head)
RDS PostgreSQL 16  ·  ElastiCache Redis  ·  S3 (assets)  ·  CloudFront (web + assets)
Secrets Manager (credentials)  ·  CloudWatch Logs (stdout)
```

**Resources**

- **ECR** repository for the backend image.
- **ECS cluster** (Fargate) with two services — `api` (behind the ALB target
  group, health check `GET /health/ready`) and `worker` (no load balancer).
- **ALB** with an HTTPS listener and an **ACM** certificate; forward `/` to
  the api target group.
- **RDS** PostgreSQL 16, private subnets, automated backups enabled.
- **ElastiCache** Redis (single node is fine to start).
- **S3** bucket for assets + **CloudFront** distribution for the web app and
  static assets.
- **Secrets Manager** entries for `SECRET_KEY`, `DATABASE_URL`,
  `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`, `AI_API_KEY`, SMTP credentials;
  inject them as ECS task-definition `secrets` (never as plaintext `environment`).
- **Parameter Store** for non-secret config (`APP_ENV`, `APP_URL`,
  `TRIAL_DURATION_DAYS`, …) if you prefer it over task-definition env vars.

**Deployment commands**

```bash
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com
docker build -t $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/tekaplay-api:$TAG backend/
docker push  $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/tekaplay-api:$TAG

# migrate first, then roll the services
aws ecs run-task --cluster tekaplay --task-definition tekaplay-migrate \
  --launch-type FARGATE --network-configuration "$NETWORK"
aws ecs update-service --cluster tekaplay --service api    --force-new-deployment
aws ecs update-service --cluster tekaplay --service worker --force-new-deployment
```

**Configuration.** `DATABASE_URL=postgresql+asyncpg://…@<rds-endpoint>/tekaplay`,
`REDIS_URL=redis://<elasticache-endpoint>:6379/0`, plus the Celery broker and
result-backend URLs on databases 1 and 2 of the same instance. Object storage
points at S3 by clearing `OBJECT_STORAGE_ENDPOINT` and setting the bucket and
region — the S3 API surface is unchanged from R2.

**Stripe webhook URL.** `https://api.<your-domain>/api/v1/commerce/webhooks/stripe`.
Register it in the Stripe dashboard, subscribe to `checkout.session.completed`,
`customer.subscription.created|updated|deleted`, `invoice.paid`,
`invoice.payment_failed`, `charge.refunded`, and put the signing secret in
Secrets Manager as `STRIPE_WEBHOOK_SECRET`.

**Logging and monitoring.** ECS `awslogs` driver → CloudWatch Logs (the JSON
structlog output is already query-friendly in Logs Insights). Alarms worth
having early: ALB 5xx rate, ECS service running-task count below desired,
RDS free storage and CPU, ElastiCache evictions.

**Backups.** RDS automated backups with point-in-time recovery; set the
retention window deliberately (7 days is the default, not a decision).
Snapshot before each migration that is not purely additive. Test a restore.

**IaC.** Not included in this repo yet — the commands above are the manual
equivalent. Terraform or CDK is the natural next step; `infra/` is the
intended home.

---

## 4. Azure

Same shape, Azure primitives.

```
Azure Front Door / DNS → Container Apps: api    (backend/Dockerfile)
                                         worker  (same image, celery command)
                         Container Apps Job:     migrate (alembic upgrade head)
Azure Database for PostgreSQL Flexible Server  ·  Azure Cache for Redis
Azure Blob Storage  ·  Key Vault  ·  Application Insights / Log Analytics
```

**Resources**

- **Azure Container Registry** for the image.
- **Container Apps** environment with two apps: `api` (ingress enabled,
  external, target port 8000, health probe `/health/ready`) and `worker`
  (ingress disabled).
- **Container Apps Job** for migrations, run to completion before rollout.
- **Azure Database for PostgreSQL Flexible Server** (v16).
- **Azure Cache for Redis** (Basic tier to start).
- **Key Vault** for `SECRET_KEY`, `DATABASE_URL`, Stripe keys, SMTP
  credentials — referenced as Container Apps secrets, never inline values.
- **Application Insights** connected to the Container Apps environment's Log
  Analytics workspace.

**Deployment commands**

```bash
az acr build --registry tekaplayacr --image tekaplay-api:$TAG backend/

az containerapp job start --name tekaplay-migrate --resource-group tekaplay

az containerapp update --name tekaplay-api    --resource-group tekaplay \
  --image tekaplayacr.azurecr.io/tekaplay-api:$TAG
az containerapp update --name tekaplay-worker --resource-group tekaplay \
  --image tekaplayacr.azurecr.io/tekaplay-api:$TAG
```

**Configuration.** Identical variable names; only the URLs change. Flexible
Server requires TLS — append `?ssl=true` to the asyncpg URL if your network
policy needs it explicitly.

**HTTPS/domain.** Container Apps issues managed certificates for custom
domains. Set `APP_URL` and `CORS_ORIGINS` to the public web origin.

**Stripe webhook URL.**
`https://api.<your-domain>/api/v1/commerce/webhooks/stripe`, same event list
and signing-secret handling as AWS.

**Logging and monitoring.** stdout flows to Log Analytics; query with KQL.
Application Insights gives request/dependency telemetry and alerting.

**Backups.** Flexible Server performs automated backups (retention
configurable, 7–35 days) with point-in-time restore. Same rule: test it.

---

## 5. Environment variables

Full list lives in `.env.example` and, authoritatively, in
`backend/app/core/config.py` — modules never read `os.environ` directly.
The ones this feature added or that matter most in production:

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `local` | `local` / `test` / `staging` / `production` |
| `APP_URL` | `http://localhost:3000` | Public web origin; builds links in outgoing emails |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Must include the web origin |
| `SECRET_KEY` | insecure default | JWT signing — **must** be set in production |
| `DATABASE_URL` | local Postgres | `postgresql+asyncpg://…` |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | local Redis | Cache, rate limiting, queue |
| `TRIAL_ENABLED` | `true` | Master switch for self-serve free trials |
| `TRIAL_DURATION_DAYS` | `14` | Trial length; change freely (7/14/30/60) |
| `PAYMENT_PROVIDER` | `fake` | `fake` (no network) or `stripe` |
| `STRIPE_API_KEY` | — | Secret key. Backend only — never exposed to the browser |
| `STRIPE_WEBHOOK_SECRET` | — | `whsec_…`; webhook signature verification |
| `STRIPE_WEBHOOK_TOLERANCE_SECONDS` | `300` | Replay window for webhook timestamps |
| `EMAIL_PROVIDER` | `console` | `console` logs instead of sending; `smtp` sends |
| `EMAIL_DISPATCH` | `inline` | `inline` or `celery` (queue sends to the worker) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` | — | Required when `EMAIL_PROVIDER=smtp` |
| `FROM_EMAIL` | `no-reply@tekaplay.app` | Envelope sender |

Stripe **price** IDs are intentionally *not* environment variables — they
live on `Plan.stripe_price_id` so plans and pricing are runtime-configurable
without a deploy. Only the account-level secrets are env vars.

Never commit real secrets. `.env` is gitignored; `.env.example` carries
placeholders only.

---

## 6. Stripe setup

1. Create products and recurring prices in the Stripe dashboard — one price
   per plan you intend to sell, including an organization/seat price
   (Stripe multiplies it by the checkout quantity).
2. Create the matching `Plan` rows via `POST /api/v1/commerce/plans` with the
   `stripe_price_id` and the right `kind` (`individual` | `organization`).
3. Register the webhook endpoint and copy its signing secret into
   `STRIPE_WEBHOOK_SECRET`.
4. Set `PAYMENT_PROVIDER=stripe` and `STRIPE_API_KEY`.

### Why webhooks are the source of truth

Checkout and portal calls create Stripe sessions but never write subscription
state. Every mutation to `subscriptions`, `payments`, and org
`enterprise_licenses` happens in `CommerceService.handle_webhook`, which:

- verifies the HMAC-SHA256 signature over `{timestamp}.{payload}` and rejects
  timestamps outside `STRIPE_WEBHOOK_TOLERANCE_SECONDS` (replay protection);
- records every processed `stripe_event_id` in the `webhook_events` ledger and
  no-ops on repeats, so Stripe's at-least-once retries are safe;
- routes on `metadata.kind` to update either a personal `Subscription` or an
  organization's `EnterpriseLicense` seat count.

Seat counts therefore come from Stripe, never from the client — the frontend
cannot inflate how many licenses an organization owns.

### Licensing concurrency

Seat allocation is transactional: `assign_license` takes a row lock on the
license (`SELECT … FOR UPDATE`) before comparing active assignments against
`seats`, so two simultaneous requests for the last seat serialize and exactly
one wins. SQLite has no row locks (SQLAlchemy omits the clause), so the
concurrency test is skipped there and runs against PostgreSQL in CI; the
capacity rule itself is covered on every dialect by
`test_license_cannot_exceed_seats`.
