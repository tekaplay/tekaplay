# Deployment

How to take this repository from a fresh clone to a live production
application, for free, without painting yourself into a corner.

Two audiences, one document: follow steps 1–9 in order to launch; read
[step 10](#step-10--future-awsazure-migration) before you outgrow the launch
stack. Day-to-day operations live in [RUNBOOK.md](RUNBOOK.md).

- [The architecture, and why](#the-architecture-and-why)
- [Prerequisites](#prerequisites)
- [Step 1 — Prepare the repository](#step-1--prepare-the-repository)
- [Step 2 — Create the database](#step-2--create-the-database)
- [Step 3 — Create Redis](#step-3--create-redis)
- [Step 4 — Authentication](#step-4--authentication)
- [Step 5 — Storage](#step-5--storage)
- [Step 6 — Environment variables](#step-6--environment-variables)
- [Step 7 — Deploy](#step-7--deploy)
- [Step 8 — Run migrations and create an admin](#step-8--run-migrations-and-create-an-admin)
- [Step 9 — Verify the deployment](#step-9--verify-the-deployment)
- [Backups](#backups)
- [Costs, and exactly when they start](#costs-and-exactly-when-they-start)
- [Step 10 — Future AWS/Azure migration](#step-10--future-awsazure-migration)
- [Local development](#local-development)

---

## The architecture, and why

The application is three processes and two data stores:

```
  browser ──▶ tekaplay-web    Next.js 14        Render (free)
                   │
                   └────────▶ tekaplay-api    FastAPI          Render (free)
                                  ├─────────▶ PostgreSQL 16    Neon   (free)
                                  └─────────▶ Redis            Upstash (free)

              tekaplay-worker   Celery         not deployed at launch —
                                               AI_DISPATCH/EMAIL_DISPATCH=inline
```

**Total cost at launch: $0/month.**

Three decisions are worth stating outright, because each one is a trade:

**Why not Vercel for the frontend.** Vercel's free (Hobby) tier prohibits
commercial use, and this application sells subscriptions. Running both services
on Render also means both deploy from the `Dockerfile`s already in this repo —
one build system, and an image that runs unchanged on ECS or Container Apps
later.

**Why not Supabase for the database.** Supabase is excellent when you use its
auth, storage and PostgREST layers. This application has its own argon2id +
JWT authentication and no file uploads, so Supabase would be serving as a plain
PostgreSQL host — a role Neon fills with a more generous free tier for that
specific job and no pooler quirks with asyncpg. Both are ordinary PostgreSQL
and both `pg_dump` identically, so this is a convenience choice, not a
lock-in one.

**Why no Celery worker.** Render has no free plan for background workers. The
application already supports running those jobs inline
(`AI_DISPATCH=inline`, `EMAIL_DISPATCH=inline`), which is correct at this
scale: the work happens inside the request that asked for it. Adding the worker
later is uncommenting a block in `render.yaml` and flipping two variables.

**What keeps this portable.** No application code imports a cloud-provider SDK.
Every dependency is a URL in `backend/app/core/config.py`; storage, email,
payments and AI each sit behind a small `Protocol` with a swappable
implementation. `render.yaml` is the only provider-specific file in the
repository, and deleting it breaks nothing but the deployment convenience.

---

## Prerequisites

### Accounts (all free, no card required to start)

| Service | Used for | Sign up |
|---|---|---|
| **GitHub** | Source of truth; Render deploys from it | github.com |
| **Render** | Hosting for both the API and the web app | render.com |
| **Neon** | PostgreSQL database | neon.tech |
| **Upstash** | Redis (rate limiting, caching) | upstash.com |

Optional, and not needed to launch: Resend (email), Stripe (payments),
Anthropic (AI drafting). Each is covered in
[step 6](#optional-integrations-and-what-they-cost).

### Software on your machine

| Tool | Version | Needed for |
|---|---|---|
| **Git** | any recent | cloning, pushing |
| **Python** | 3.12 or 3.13 | running migrations, creating the admin |
| **Node.js** | 20 | frontend development |
| **PostgreSQL client** | 16 | `pg_dump` / `pg_restore` for backups |
| **Docker** | any recent | *optional* — local all-in-one development |

> Python 3.14+ is not currently usable for local backend work: `psycopg2-binary`
> and `asyncpg` do not yet publish wheels for it and fall back to a source build
> that fails without PostgreSQL headers. Deployment is unaffected — the Docker
> image pins 3.12.

Check what you have:

```bash
git --version
python --version      # must report 3.12.x or 3.13.x
node --version        # must report v20.x
pg_dump --version     # must report 16.x
```

### Permissions

You need admin rights on the GitHub repository (to connect Render) and owner
access on each of the four accounts above.

---

## Step 1 — Prepare the repository

```bash
git clone https://github.com/<your-org>/Tekaplay.git
cd Tekaplay
```

Install the backend dependencies (a virtual environment keeps them off your
system Python):

```bash
cd backend
python -m venv .venv

# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
cd ..
```

Install the frontend dependencies:

```bash
cd frontend
npm ci
cd ..
```

Create your local configuration file. It is gitignored and never leaves your
machine:

```bash
cp .env.example .env
```

Confirm the checkout is sound before deploying anything:

```bash
cd backend && pytest -q && cd ..
cd frontend && npm run lint && npm run typecheck && npm test && cd ..
```

---

## Step 2 — Create the database

### 2.1 Create it

1. Sign in at [console.neon.tech](https://console.neon.tech).
2. **Create project.**
   - **Name:** `tekaplay`
   - **PostgreSQL version:** `16` — this is what the migrations are tested on.
   - **Region:** whichever is closest to your users. Pick the same region you
     will pick for Render in step 7; a database on another continent adds
     latency to *every single query*.
3. Neon creates a database named `neondb`. That is fine — nothing depends on
   the name.

### 2.2 Get the connection string

On the project dashboard, **Connection Details**:

- Choose the **Pooled connection** (the host contains `-pooler`). Pooling
  matters here: serverless Postgres has a low direct-connection ceiling, and
  the API opens a pool of its own.
- Copy the string. It looks like:

```
postgresql://neondb_owner:npg_XXXXXXXX@ep-cool-name-123456-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
```

**Paste it exactly as given, including `?sslmode=require`.** You do not need to
convert it to a `+asyncpg` URL. `backend/app/db/url.py` translates it at
startup — asyncpg cannot parse `sslmode`, but Alembic's synchronous driver
requires it, so the application derives both forms from the one string.

Treat this value as a password, because it contains one.

### 2.3 Enable the largest history window you can

Neon console → **Settings → Storage → History retention**. Raise it to the
maximum the free plan allows. This is your point-in-time-restore window, and it
costs nothing until you exceed the storage allowance.

This is *not* a substitute for real backups — see [Backups](#backups).

Migrations are run in [step 8](#step-8--run-migrations-and-create-an-admin),
once the rest of the configuration exists.

---

## Step 3 — Create Redis

Redis is used for rate limiting, the AI response cache, and OAuth state. The
rate limiter **fails open** — if Redis is unreachable, requests still succeed
and a warning is logged — so this is not a hard dependency. Set it up anyway:
without it, the brute-force protection on the login endpoint does nothing.

1. Sign in at [console.upstash.com](https://console.upstash.com).
2. **Create database** → type **Redis**.
   - **Name:** `tekaplay`
   - **Region:** as close to your Render region as possible.
   - **TLS:** enabled (the default).
3. From the database page copy the **`rediss://`** URL — note the double `s`,
   which means TLS.

```
rediss://default:XXXXXXXX@apt-thrush-12345.upstash.io:6379
```

This also contains a password. Treat it accordingly.

---

## Step 4 — Authentication

Authentication is built into the application — there is no third-party auth
provider to configure, and no per-user fee to grow into. Users, roles,
permissions, sessions and refresh tokens are all rows in *your* PostgreSQL
database, which is the single most important property for portability: moving
providers never means migrating an identity service.

How it works, in brief:

- Passwords are hashed with **argon2id** (`backend/app/core/security.py`) and a
  minimum length of 10 characters is enforced.
- The API issues a short-lived **JWT access token** (15 minutes) plus an opaque
  **refresh token** (30 days). Refresh tokens are stored **hashed** (SHA-256),
  so a database leak yields no usable credentials.
- Refresh tokens **rotate** on use, and replaying a rotated token revokes the
  entire token family — that is theft detection.
- Login, registration and password-reset are rate limited per IP *and* per
  email address.

**What you must configure:** exactly one variable, `SECRET_KEY`, which signs
access tokens. `render.yaml` has Render generate it, so there is nothing to do
by hand. If you set it yourself, use `openssl rand -hex 32`. Rotating it logs
every user out immediately.

**Optional — social login.** Google and Microsoft OAuth are implemented and
disabled by default. To enable, register an OAuth application with the provider
and set `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` (and/or the
`MICROSOFT_*` pair). The redirect URI is `https://<your-web-host>/login`.

**Note on email verification.** Because email is disabled at launch
([step 6](#optional-integrations-and-what-they-cost)), users cannot verify
their address or reset a forgotten password. Registration and login work
normally. This is the first thing to turn on after launch.

---

## Step 5 — Storage

**Nothing to configure. The application stores no files.**

There are no upload endpoints and no user-supplied binaries anywhere in the
codebase — mission content is JSON in PostgreSQL, so it is covered by the
database backup like everything else.

The `OBJECT_STORAGE_*` variables in `.env.example` are a declared, unused seam
for when uploads are added. They describe an **S3-compatible** interface, which
means the eventual choice between Cloudflare R2, AWS S3, MinIO, or Azure Blob
via its S3 gateway is a configuration change. Leave them blank.

---

## Step 6 — Environment variables

The complete, annotated list is [`.env.example`](../.env.example); the
authoritative definition is `backend/app/core/config.py`. Nothing in the
application reads `os.environ` directly, which is what makes changing providers
a configuration change rather than a code change.

Variables fall into three classes, and the distinction matters:

| Class | Meaning | Where it may live |
|---|---|---|
| **`[public]`** | Compiled into the browser bundle. Assume the world can read it. | Anywhere. Never put a credential here. |
| **`[server]`** | Server-side only, but not itself a credential. | Render environment variables. |
| **`[SECRET]`** | A credential. | Render environment variables only — never in Git, never in a `NEXT_PUBLIC_` variable, never in a log. |

### What to set on `tekaplay-api`

| Variable | Value | Class |
|---|---|---|
| `APP_ENV` | `production` | server |
| `SECRET_KEY` | *generated by Render* | **SECRET** |
| `APP_URL` | `https://tekaplay-web.onrender.com` | server |
| `CORS_ORIGINS` | `["https://tekaplay-web.onrender.com"]` | server |
| `DATABASE_URL` | the Neon string from step 2 | **SECRET** |
| `REDIS_URL` | the Upstash string from step 3 | **SECRET** |
| `AI_DISPATCH` | `inline` | server |
| `EMAIL_DISPATCH` | `inline` | server |
| `LOG_LEVEL` | `INFO` | server |
| `FORWARDED_ALLOW_IPS` | `*` | server |

`FORWARDED_ALLOW_IPS` is read by uvicorn, not by the application, and matters
more than it looks: Render terminates TLS at its own proxy, so without it every
request appears to originate from that proxy. Per-IP rate limiting would then
throttle all users as a single bucket, and audit logs would record the proxy's
address instead of the user's. `*` is safe here because the container is only
reachable through Render — do not carry it to a deployment whose port is
exposed directly.

`CORS_ORIGINS` must be a **JSON array** and must match `APP_URL` exactly —
scheme included, no trailing slash. A mismatch produces the single most
confusing failure mode in web deployment: the API answers `curl` perfectly
while every browser request fails, because `curl` does not enforce CORS and
browsers do.

### What to set on `tekaplay-web`

| Variable | Value | Class |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `https://tekaplay-api.onrender.com/api/v1` | public |

This one is **compiled into the JavaScript at build time**. Changing it and
restarting achieves nothing — it requires a rebuild.

### Production guardrails

With `APP_ENV=production`, the application **refuses to start** on a
configuration that would be unsafe: a default or short `SECRET_KEY`, a wildcard
or non-HTTPS CORS origin, a SQLite or localhost database, a plain-HTTP
`APP_URL`, or an integration enabled without its credential.

The failure message names every problem at once. This is deliberate: a
misconfigured production deploy should die loudly at startup rather than
quietly serve traffic signed with a publicly known key.

Check a configuration without deploying it:

```bash
cd backend
python -m app.scripts.check_env
```

It reports what the application resolved, with credentials redacted, and warns
about integrations still running in no-op mode.

### Optional integrations, and what they cost

All three default to a safe no-op. Each becomes real by changing one variable
and supplying a credential — no code change.

| | Default | To enable | Cost |
|---|---|---|---|
| **Email** | `EMAIL_PROVIDER=console` — messages are written to the logs, never delivered | Set `EMAIL_PROVIDER=smtp` and the `SMTP_*` variables. [Resend](https://resend.com) works with the built-in SMTP sender: host `smtp.resend.com`, port `587`, username `resend`, password = your API key. You must verify a sending domain. | Free to 3,000/month, then ~$20/mo |
| **Payments** | `PAYMENT_PROVIDER=fake` — deterministic, no network | See [Stripe setup](#stripe-setup) | 2.9% + 30¢ per transaction |
| **AI drafting** | `AI_PROVIDER=echo` — returns placeholder text | Set `AI_PROVIDER=anthropic` and `AI_API_KEY` | Pay per token, no free tier |

**Until email is enabled, password reset and emailed invitations do not work.**
Registration, login and organization membership all work; only the emailed
links are missing (they appear in the API logs instead). Budget ten minutes to
turn this on shortly after launch.

---

## Step 7 — Deploy

The repository contains [`render.yaml`](../render.yaml), a Blueprint that
declares both services. Using it means the deployment is reproducible rather
than a sequence of remembered dashboard clicks.

### 7.1 Set the region, then push to GitHub

`render.yaml` specifies `region: oregon` for both services. **Change it if your
Neon database is elsewhere** — a database on another continent adds latency to
every query. Valid values include `oregon`, `ohio`, `virginia`, `frankfurt`,
and `singapore`.

```bash
git add -A
git commit -m "Production deployment configuration"
git push origin main
```

### 7.2 Create the Blueprint

1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**.
2. Connect your GitHub account and select the `Tekaplay` repository.
3. Render reads `render.yaml` and shows two services: `tekaplay-api` and
   `tekaplay-web`.
4. It prompts for every variable marked `sync: false`. You do not yet know the
   final URLs, so use the names Render will assign — they are predictable:

   | Prompt | Value |
   |---|---|
   | `APP_URL` | `https://tekaplay-web.onrender.com` |
   | `CORS_ORIGINS` | `["https://tekaplay-web.onrender.com"]` |
   | `DATABASE_URL` | Neon string from step 2 |
   | `REDIS_URL` | Upstash string from step 3 |
   | `NEXT_PUBLIC_API_BASE_URL` | `https://tekaplay-api.onrender.com/api/v1` |

   If Render assigns different names (it appends a suffix when a name is
   taken), correct these afterwards under each service's **Environment** tab —
   and rebuild the web service, since its variable is build-time.

5. **Apply.** The first build takes roughly 5–10 minutes.

### 7.3 Set the web service's build argument

`NEXT_PUBLIC_API_BASE_URL` must reach the **Docker build**, not just the
runtime. On `tekaplay-web` → **Settings → Build & Deploy → Docker Build
Arguments**, add:

```
NEXT_PUBLIC_API_BASE_URL = https://tekaplay-api.onrender.com/api/v1
```

Then **Manual Deploy → Clear build cache & deploy**.

Skipping this is the most likely thing to go wrong: the app deploys, loads, and
every API call goes to `localhost:8000`, which fails silently in the browser
console.

### 7.4 Domains

Both services get an HTTPS URL with a managed certificate automatically:
`https://tekaplay-api.onrender.com`, `https://tekaplay-web.onrender.com`.

To attach a custom domain later: service → **Settings → Custom Domains** → add
the domain and create the CNAME record Render shows you. Then update `APP_URL`
and `CORS_ORIGINS` on the API, update `NEXT_PUBLIC_API_BASE_URL` on the web
service, and **rebuild the web service**.

---

## Step 8 — Run migrations and create an admin

The database is still empty. Migrations are deliberately *not* run
automatically on deploy, so a schema change is never an accidental side effect
of a code push.

From your machine, with the virtual environment from step 1 active:

```bash
cd backend

# macOS / Linux
export DATABASE_URL='postgresql://neondb_owner:...@ep-...-pooler...neon.tech/neondb?sslmode=require'
# Windows PowerShell
$env:DATABASE_URL='postgresql://neondb_owner:...@ep-...-pooler...neon.tech/neondb?sslmode=require'

alembic upgrade head
```

Expected output ends with `Running upgrade 0006 -> 0007, ...`. Confirm:

```bash
alembic current       # prints 0007 (head)
```

Create the first administrator — pick a real password, this account can do
everything:

```bash
python -m app.scripts.create_admin you@example.com 'your-long-password-here'
```

Optionally load the demo mission so the library is not empty:

```bash
python -m app.scripts.seed_demo
```

Take your first backup now, before any customer data exists, to prove the
process works while the stakes are zero:

```bash
cd .. && ./scripts/backup.sh
```

---

## Step 9 — Verify the deployment

Work through this list in order. The first request will take ~50 seconds while
the free service wakes — that is a cold start, not a failure.

### Infrastructure

- [ ] `curl https://tekaplay-api.onrender.com/api/v1/health/live` → `{"status":"ok"}`
- [ ] `curl https://tekaplay-api.onrender.com/api/v1/health/ready` → `{"status":"ready"}`
      *(this one proves the database is reachable)*
- [ ] `https://tekaplay-api.onrender.com/api/v1/docs` returns **404** —
      API docs must be off in production
- [ ] The homepage loads at `https://tekaplay-web.onrender.com`

### Authentication

- [ ] **Register** a new account — the form submits and you land logged in
- [ ] **Log out** — you are returned to the signed-out state
- [ ] **Log in** with the same credentials
- [ ] Log in with a *wrong* password → a clear error, no stack trace, no hint
      as to whether the email exists
- [ ] Enter a wrong password ~11 times → HTTP 429. *(This confirms Redis is
      connected. If it never throttles, `REDIS_URL` is wrong — the limiter
      fails open by design.)*
- [ ] Refresh the page while logged in → still logged in
- [ ] Leave it 20 minutes, then act → the access token refreshes silently

### Core workflow

- [ ] The mission library lists content *(run `seed_demo` if empty)*
- [ ] Start a mission — the session loads
- [ ] Complete a challenge — **database write**
- [ ] Reload mid-mission → progress is still there — **database read**
- [ ] XP and achievements update on the dashboard
- [ ] The leaderboard renders

### Organizations

- [ ] Create an organization → you become `owner`
- [ ] Invite a second email → succeeds *(the link is in the API logs, not an
      inbox, until email is enabled)*
- [ ] Accept the invite in a second browser/incognito window at
      `/invite/<token>`
- [ ] A non-member cannot see the organization — **authorization check**

### Security

- [ ] Response headers include `X-Content-Type-Options`, `X-Frame-Options`,
      `Strict-Transport-Security`:
      ```bash
      curl -sI https://tekaplay-api.onrender.com/api/v1/health/live
      ```
- [ ] Requesting a resource belonging to another user returns 403/404, not data
- [ ] `curl https://tekaplay-api.onrender.com/api/v1/users/me` without a token
      → 401
- [ ] Error responses carry a `request_id` and no internal details
- [ ] Logs contain no passwords or tokens *(structlog redacts them)*

### Mobile

- [ ] The homepage, login and one mission are usable at 375px wide
- [ ] No horizontal scrolling

### Not applicable at launch

- File uploads — the application stores no files
- Email delivery — disabled; verify after enabling it
- Live payments — `PAYMENT_PROVIDER=fake`

---

## Backups

### What is protected, and against what

| Mechanism | Protects against | Does not protect against |
|---|---|---|
| **Neon history retention** (automatic) | A bad `UPDATE`, an accidental delete, a botched migration — anything in the last few hours | Losing the Neon account; the provider failing |
| **`./scripts/backup.sh`** (manual) | Everything above, *plus* provider loss, billing suspension, account lockout | Nothing, if you never run it |

Relying only on the first is the mistake worth avoiding. A provider's backups
live inside that provider.

### Taking a backup

```bash
export DATABASE_URL='postgresql://…?sslmode=require'
./scripts/backup.sh
# → backups/tekaplay-20260816T101500Z.dump
```

This writes a `pg_dump` custom-format archive — compressed, restorable
table-by-table, and loadable into **any** PostgreSQL 16 server. It refuses to
report success if the archive contains no table data.

`backups/` is gitignored. **Copy the file somewhere else** — a cloud drive, an
external disk, a private bucket. A backup that exists in exactly one place is
not a backup.

### How often

At fewer than 50 customers: **weekly**, plus **before every migration**. Move
to daily once losing a day of data would matter.

To automate it, add a GitHub Actions scheduled workflow that runs the script
and uploads the artifact — but store `DATABASE_URL` as a repository secret and
be aware that Actions artifacts are visible to anyone with repository access.

### Restoring, and verifying

Full procedure, including a restore drill you should run *before* you need it:
[RUNBOOK.md §8](RUNBOOK.md#8-restore-the-database). The short version:

```bash
pg_restore --clean --if-exists --no-owner -d "$TARGET_DATABASE_URL" <archive>.dump
```

Run the drill at least once. An untested backup is a hypothesis, not a backup.

---

## Costs, and exactly when they start

Everything below starts at **$0/month**. Free-tier limits change — treat the
numbers as a starting point to verify, and the *signals* as the durable part.

### What you are consuming

| Service | Free allowance (verify current) | What happens at the limit | Watch |
|---|---|---|---|
| **Render** web services | 750 instance-hours/month **shared across your whole account**; sleeps after 15 min idle; 100 GB egress | Services stop until the month resets | Dashboard → Billing |
| **Neon** | ~0.5 GB storage, limited monthly compute hours | Compute suspends; storage overage blocks writes | Neon → Usage |
| **Upstash** | Daily command cap on the free plan | Commands rejected — the rate limiter fails open, so the app keeps working | Upstash → Usage |

Two Render services sleeping when idle fit comfortably in 750 hours at low
traffic. Two services *staying awake* would need ~1,460 hours and would not.

### Concrete upgrade triggers, in the order you will hit them

**1. Cold starts become a real complaint — probably first.**
The free plan sleeps after 15 minutes. Any user arriving after a quiet period
waits ~50 seconds. With fewer than 50 customers spread across a day, this is
*most* visits.

→ **Render Starter, $7/month per service.** Upgrade the **web** service first
(users see it), the API second. **~$14/month** for both. This is a dashboard
toggle, no code change. Honestly: if you have paying customers, do this at
launch.

**2. Someone needs a password reset.**
→ Enable email (step 6). **Free** to 3,000/month with Resend.

**3. AI drafting is used for real.**
→ Set `AI_PROVIDER=anthropic`. Pay-per-token; no free tier. Budget by usage,
and note the built-in per-user rate limit (`AI_RATE_LIMIT_PER_MINUTE`).

**4. AI calls make requests feel slow, or email sending delays a response.**
Inline dispatch means the user waits for the work. Once that is noticeable:
→ Uncomment the worker in `render.yaml`, set `AI_DISPATCH=celery` and
`EMAIL_DISPATCH=celery`. **+$7/month.**

**5. Neon's free storage or compute runs out.**
At ~50 customers this is unlikely — the data is text. Check *Usage* monthly.
→ Neon's paid plan starts around **$19/month**.

**6. You take real payments.**
→ Stripe. No fixed fee; per-transaction only.

### Realistic trajectory

| Stage | Monthly |
|---|---|
| Launch, pilot users, cold starts tolerated | **$0** |
| Paying customers, no cold starts | **~$14** |
| Email enabled | **~$14** |
| Background worker added | **~$21** |
| Outgrowing the Neon free tier | **~$40** |

### When to leave this stack entirely

Not at 50 customers. Not at 500. Move to AWS or Azure when one of these is
*actually* true:

- Monthly spend passes roughly **$200–300** — dedicated infrastructure starts
  to compete on price, though not on your time.
- **Compliance** requires a specific region, a signed BAA, or data-residency
  guarantees the free-tier providers will not sign.
- You need **VPC-private networking**, custom IAM, or a private database.
- Traffic needs **multi-region** or autoscaling beyond what Render offers.
- Your organization has an existing AWS/Azure commitment to draw down.

Migrating early costs weeks of engineering to solve problems you do not have.

---

## Step 10 — Future AWS/Azure migration

The point of the launch architecture is that this section describes a
*migration*, not a rewrite.

### Where everything lives today

| Concern | Today | Coupling |
|---|---|---|
| Application code | `backend/`, `frontend/` | **None.** No cloud SDK is imported anywhere. |
| API + web processes | Render, from the repo's Dockerfiles | **None.** Standard OCI images. |
| Database | Neon | **None.** Stock PostgreSQL 16; no proprietary extensions. |
| Redis | Upstash | **None.** Standard Redis protocol. |
| Files | — | **None.** Nothing is stored. |
| Identity | Your PostgreSQL database | **None.** No third-party auth provider. |
| Email / payments / AI | Behind `Protocol` interfaces | **None.** Config-selected implementations. |
| Deployment config | `render.yaml` | **Total** — and it is 90 lines you delete. |

### What must be deliberately migrated

Be honest about this list; nothing here is automatic:

1. **The data.** A `pg_dump`/`pg_restore` cycle — routine, but it needs a
   maintenance window sized to your database.
2. **DNS.** Cut-over plus propagation.
3. **Secrets.** Re-entered into Secrets Manager or Key Vault. `SECRET_KEY` must
   be **carried over, not regenerated**, or every user is logged out.
4. **Stripe webhook URL.** Re-register at the new domain, and copy the new
   signing secret.
5. **Infrastructure-as-code.** None exists yet; `infra/` is the intended home.
   This is the real cost of the move — days, not hours.
6. **Log and metric tooling.** Different query languages, rebuilt dashboards.

### What does **not** change

Application code. The schema. The migrations. The Docker images. Every
environment variable name. Auth. Business logic.

### Sequence, minimising downtime

```
1. Build the target infrastructure alongside production (nothing switches).
2. Restore a recent dump into the new database; run `alembic upgrade head`.
3. Deploy the same images to the new compute; point them at the new database.
4. Smoke-test the new stack on a temporary hostname, with real data.
5. Lower the DNS TTL to 60s. Wait for the old TTL to expire.
6. ── Maintenance window opens ──
     a. Suspend the old API (writes stop).
     b. Final dump → restore into the new database (only the delta since 2).
     c. Switch DNS.
     d. Update APP_URL, CORS_ORIGINS, and Stripe's webhook URL.
   ── Window closes: minutes, dominated by the size of the delta ──
7. Watch logs and error rates. Keep the old stack suspended, not deleted,
   for a week — it is your rollback.
```

### Target architecture — AWS

Deliberately no Lambda/API Gateway split: this is a long-lived ASGI server, not
a set of functions. Deliberately no Aurora: RDS is cheaper and sufficient.

```
Route 53 → ACM → ALB → ECS Fargate: api    (backend/Dockerfile)
                        ECS Fargate: web    (frontend/Dockerfile)
                        ECS task:    migrate (alembic upgrade head)
RDS PostgreSQL 16 · ElastiCache Redis · Secrets Manager · CloudWatch Logs
```

- **ECR** for both images.
- **ECS Fargate** services; the API's target group health check is
  `/api/v1/health/live`.
- **RDS** PostgreSQL 16 in private subnets, automated backups on.
- **Secrets Manager** for `SECRET_KEY`, `DATABASE_URL`, Stripe and SMTP
  credentials, injected as task-definition `secrets` — never plaintext
  `environment`.
- Configuration changes: `DATABASE_URL` to the RDS endpoint, `REDIS_URL` to
  ElastiCache. Nothing else.

```bash
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com
docker build -t $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/tekaplay-api:$TAG backend/
docker push  $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/tekaplay-api:$TAG

aws ecs run-task --cluster tekaplay --task-definition tekaplay-migrate \
  --launch-type FARGATE --network-configuration "$NETWORK"
aws ecs update-service --cluster tekaplay --service api --force-new-deployment
```

Alarms worth having from day one: ALB 5xx rate, ECS running-task count below
desired, RDS free storage and CPU.

### Target architecture — Azure

```
Front Door / DNS → Container Apps: api, web  (the same Dockerfiles)
                   Container Apps Job:  migrate
Azure Database for PostgreSQL Flexible Server 16 · Azure Cache for Redis
Key Vault · Log Analytics / Application Insights
```

- **ACR** for the images; **Container Apps** with external ingress on the API
  (target port 8000, probe `/api/v1/health/live`).
- **Container Apps Job** for migrations, run to completion before rollout.
- **Key Vault** referenced as Container Apps secrets, never inline.
- Flexible Server requires TLS; keep `?sslmode=require` on the URL —
  `app/db/url.py` already handles it.

```bash
az acr build --registry tekaplayacr --image tekaplay-api:$TAG backend/
az containerapp job start --name tekaplay-migrate --resource-group tekaplay
az containerapp update --name tekaplay-api --resource-group tekaplay \
  --image tekaplayacr.azurecr.io/tekaplay-api:$TAG
```

### Exporting your data, at any time, from anywhere

```bash
./scripts/backup.sh                                    # portable archive
pg_restore --clean --if-exists --no-owner -d "$NEW_DATABASE_URL" <archive>.dump
```

That is the whole export path. There is no proprietary format, no vendor API,
and no dashboard in the loop.

---

## Local development

```bash
cp .env.example .env
docker compose up --build
```

Compose starts PostgreSQL 16, Redis 7, a one-shot `migrate` service (which
gates the API and worker on its success), the API, a Celery worker, and the web
app.

- API — http://localhost:8000 (docs at `/api/v1/docs`)
- Web — http://localhost:3000
- PostgreSQL on host port **5433**, Redis on **6380** — offset deliberately so
  they do not collide with local installs.

```bash
docker compose exec api python -m app.scripts.create_admin admin@example.com <password>
docker compose exec api python -m app.scripts.seed_demo
```

### Without Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate    # Python 3.12 or 3.13
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

```bash
cd frontend
npm ci
npm run dev
```

### Tests, lint, types

```bash
cd backend && pytest                      # full suite, SQLite, no infrastructure
cd backend && ruff check app tests && mypy app
cd frontend && npm run lint && npm run typecheck && npm test && npm run build
```

Two concurrency tests are skipped on SQLite and run for real when
`DATABASE_URL` points at PostgreSQL, which is what CI does:

- `test_concurrent_assignment_of_last_seat` — SQLite has no row locks, so
  `SELECT … FOR UPDATE` is silently omitted.
- `test_optimistic_concurrency_conflict` — SQLite's file-level locking does not
  give two sessions genuinely isolated snapshots.

Each is skipped with the reason inline. Everything else runs on SQLite with no
infrastructure at all.

---

## Stripe setup

Only needed when you start charging.

1. Create products and recurring prices in the Stripe dashboard — one price per
   plan, including an organization/seat price (Stripe multiplies it by the
   checkout quantity).
2. Create matching `Plan` rows via `POST /api/v1/commerce/plans` with the
   `stripe_price_id` and the correct `kind` (`individual` | `organization`).
   Price IDs live on rows, not in environment variables, so pricing changes
   never require a deploy.
3. Register the webhook endpoint:
   `https://tekaplay-api.onrender.com/api/v1/commerce/webhooks/stripe`
   Subscribe to `checkout.session.completed`,
   `customer.subscription.created|updated|deleted`, `invoice.paid`,
   `invoice.payment_failed`, `charge.refunded`.
4. Set `PAYMENT_PROVIDER=stripe`, `STRIPE_API_KEY`, and `STRIPE_WEBHOOK_SECRET`.

```bash
# Individual monthly plan
curl -X POST https://tekaplay-api.onrender.com/api/v1/commerce/plans \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"code":"individual-monthly","name":"Individual","price_cents":1500,
       "interval":"month","trial_days":0,"kind":"individual",
       "stripe_price_id":"price_..."}'
```

### Why webhooks are the source of truth

Checkout and portal calls create Stripe sessions but never write subscription
state. Every mutation to `subscriptions`, `payments` and `enterprise_licenses`
happens in `CommerceService.handle_webhook`, which verifies the HMAC-SHA256
signature over `{timestamp}.{payload}`, rejects timestamps outside
`STRIPE_WEBHOOK_TOLERANCE_SECONDS` (replay protection), and records every
processed `stripe_event_id` in a ledger so Stripe's at-least-once retries are
idempotent.

Seat counts therefore come from Stripe, never from the client — the frontend
cannot inflate how many licenses an organization owns.

### Testing webhooks locally

`PAYMENT_PROVIDER=fake` needs no Stripe account: the fake gateway returns
deterministic ids and accepts the literal signature `fake-signature`, so the
verification path is still exercised.

```bash
stripe login
stripe listen --forward-to localhost:8000/api/v1/commerce/webhooks/stripe
# copy the printed whsec_... into STRIPE_WEBHOOK_SECRET, then:
stripe trigger customer.subscription.updated
```
