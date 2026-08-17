# Infrastructure

## What is deployed today

The launch stack, declared in [`../render.yaml`](../render.yaml) and documented
step by step in [`../docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md):

| Concern | Provider | Cost | Swap-to (AWS) | Swap-to (Azure) |
|---|---|---|---|---|
| API process | Render web service (Docker) | free | ECS Fargate | Container Apps |
| Web process | Render web service (Docker) | free | ECS Fargate + CloudFront | Container Apps |
| PostgreSQL | Neon | free | RDS PostgreSQL | Flexible Server |
| Redis | Upstash | free | ElastiCache | Azure Cache for Redis |
| Queue | *not deployed* — inline dispatch | — | SQS or the same Celery worker | Service Bus or the same worker |
| Object storage | *not used* — no file uploads exist | — | S3 | Blob (S3 gateway) |
| Secrets | Render environment variables | free | Secrets Manager | Key Vault |
| Logs | Render (stdout, ~7 days) | free | CloudWatch Logs | Log Analytics |

**Total: $0/month.** Upgrade triggers and their costs are enumerated in
[DEPLOYMENT.md → Costs](../docs/DEPLOYMENT.md#costs-and-exactly-when-they-start).

## Why this is not lock-in

Every provider above is consumed through a neutral interface — a SQLAlchemy
URL, a Redis URL, an S3-compatible client, a Celery broker URL — and email,
payments and AI each sit behind a small `Protocol` in the application with a
config-selected implementation.

Consequently `render.yaml` is the **only** provider-specific file in the
repository. Both services build from ordinary Dockerfiles, so the same images
run unchanged on ECS Fargate or Container Apps. Migration is IaC plus a
`pg_dump`/`pg_restore` cycle — not an application rewrite. The full sequence,
including what genuinely must be redone by hand, is in
[DEPLOYMENT.md → Step 10](../docs/DEPLOYMENT.md#step-10--future-awsazure-migration).

## Not here yet

**Terraform/CDK modules.** This directory is their intended home. Writing them
before there is infrastructure worth reproducing would be premature; the manual
commands in DEPLOYMENT.md are the current equivalent, and converting them is
the first task of any AWS/Azure move.

**Monitoring beyond logs.** Render's dashboard and structlog's JSON output to
stdout are sufficient at this scale. Sentry's free tier is the obvious first
addition when it stops being sufficient.
