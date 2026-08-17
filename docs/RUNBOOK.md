# Operational runbook

Day-to-day operations for the running production system. For first-time setup
see [DEPLOYMENT.md](DEPLOYMENT.md).

Assumed topology (the launch posture): two Render web services (`tekaplay-api`,
`tekaplay-web`), a Neon PostgreSQL database, an Upstash Redis instance.

| Task | Jump to |
|---|---|
| Deploy a new version | [§1](#1-deploy-a-new-version) |
| Roll back | [§2](#2-roll-back-a-deployment) |
| Run a migration | [§3](#3-run-a-database-migration) |
| Add or change an env var | [§4](#4-add-or-change-an-environment-variable) |
| View logs | [§5](#5-view-logs) |
| Check health | [§6](#6-check-application-health) |
| Back up the database | [§7](#7-back-up-the-database) |
| Restore the database | [§8](#8-restore-the-database) |
| Create a new production database | [§9](#9-create-a-new-production-database) |
| Rotate secrets | [§10](#10-rotate-secrets) |
| Troubleshoot a failed deployment | [§11](#11-troubleshoot-a-failed-deployment) |

---

## 1. Deploy a new version

`autoDeploy: true` is set in `render.yaml`, so **pushing to `main` deploys**.

```bash
git push origin main
```

Both services rebuild independently. Watch progress in the Render dashboard
under each service → *Events*.

**If the release contains a migration, run it first** (§3). Deploying code that
expects a column the database does not have will 500 every affected request.

The safe ordering for a schema change is the same one every mature system uses:

1. Ship a migration that is **additive only** (new nullable columns, new
   tables). Old code keeps working against it.
2. Deploy the code that uses the new shape.
3. In a *later* release, ship the destructive part (drop the old column).

Never combine steps 1 and 3. That is what makes a rollback survivable.

To deploy without pushing code (e.g. after changing an environment variable):
Render dashboard → service → **Manual Deploy** → *Deploy latest commit*.

---

## 2. Roll back a deployment

Render dashboard → service → **Events** → find the last good deploy →
**Rollback to this version**. This redeploys the previous image; it takes about
as long as a normal deploy.

**A rollback does not undo a migration.** If the bad release migrated the
database, roll back the code first (to stop the bleeding), then decide
deliberately whether to reverse the schema:

```bash
# Only if the migration is genuinely reversible and you have a backup.
alembic downgrade -1
```

Take a backup (§7) *before* the downgrade. Alembic downgrades that drop columns
destroy the data in them, permanently and without asking.

---

## 3. Run a database migration

Migrations are not run automatically — that is deliberate, so a schema change is
never a side effect of a code push.

From your machine, with `DATABASE_URL` pointing at production:

```bash
cd backend
export DATABASE_URL='postgresql://…?sslmode=require'   # Neon connection string
alembic current                 # what is applied now
alembic history --verbose       # what exists
alembic upgrade head            # apply everything pending
```

Or from a Render shell (dashboard → `tekaplay-api` → **Shell**, paid plans only):

```bash
alembic upgrade head
```

**Always back up first** (§7). Always check `alembic current` afterwards.

To create a new migration during development:

```bash
cd backend
alembic revision --autogenerate -m "add widget table"
# READ THE GENERATED FILE. Autogenerate misses renames, constraints and data
# migrations, and will happily generate a drop-then-create that loses data.
alembic upgrade head
```

---

## 4. Add or change an environment variable

Render dashboard → service → **Environment** → *Add Environment Variable* →
**Save Changes**. Saving triggers an automatic restart.

Two traps specific to this application:

- **`NEXT_PUBLIC_API_BASE_URL` is a build-time value.** It is compiled into the
  JavaScript bundle. Changing it in the dashboard and restarting does nothing —
  you must trigger a **rebuild** (Manual Deploy → *Clear build cache & deploy*).
- **`CORS_ORIGINS` must be valid JSON**, e.g. `["https://tekaplay-web.onrender.com"]`.
  A bare string is rejected at startup and the service will not boot.

Also add the variable to `.env.example` (without a value) so the next person
knows it exists.

Verify what the running service thinks it has:

```bash
# In a Render shell, or locally with the same env:
python -m app.scripts.check_env
```

---

## 5. View logs

Render dashboard → service → **Logs** (live tail, searchable, ~7 days retained
on the free plan).

Logs are JSON in production (structlog), one object per line, and every line
carries `request_id`, plus `user_id` once authenticated. To trace one request
end to end, search for its `request_id` — the API returns it in the
`x-request-id` response header of every response, including errors.

```bash
# What the client sees on an error:
{"error": {"code": "...", "message": "...", "request_id": "abc123"}}
# Search the logs for abc123 to get the full story, including the traceback.
```

Useful searches:

| Looking for | Search |
|---|---|
| Unhandled 500s (with tracebacks) | `unhandled_error` |
| Handled application errors | `app_error` |
| Failed logins | `auth.login` |
| Emails that were logged instead of sent | `email_sent_console` |
| Rate limiting kicking in | `rate_limited` |
| Redis outage (limiter failing open) | `rate_limit_backend_unavailable` |

Retention is short. If something matters beyond a week, export it.

---

## 6. Check application health

```bash
# Process is alive. Never touches the database.
curl -s https://tekaplay-api.onrender.com/api/v1/health/live
# {"status":"ok"}

# Dependencies reachable. Executes SELECT 1 against PostgreSQL.
curl -s https://tekaplay-api.onrender.com/api/v1/health/ready
# {"status":"ready"}
```

`/health/live` is what Render's health check polls, deliberately: pointing the
check at `/ready` would run a query every few seconds, keeping Neon's compute
permanently awake and burning the free compute-hour allowance for nothing.

On the free plan the first request after 15 minutes of inactivity takes ~50s
while the service wakes. That is a cold start, not an outage.

A full smoke test:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://tekaplay-web.onrender.com
curl -s https://tekaplay-api.onrender.com/api/v1/health/ready
```

---

## 7. Back up the database

**Automatic:** Neon retains a point-in-time restore window (length depends on
plan — on free tiers it is short, measured in hours to a day). This covers
"someone ran a bad UPDATE ten minutes ago". It does **not** cover losing access
to the Neon account itself.

**Manual, and the one that actually protects you:**

```bash
export DATABASE_URL='postgresql://…?sslmode=require'
./scripts/backup.sh
# → backups/tekaplay-20260816T101500Z.dump
```

This writes a `pg_dump` custom-format archive that restores into any PostgreSQL
16 server anywhere. Copy it off the machine — cloud drive, external disk, a
private bucket. A backup that exists in one place is not a backup.

Run this **before every migration** and on a schedule you will actually keep.
Weekly is a reasonable floor at fewer than 50 customers; daily once revenue
depends on it.

**Verify it** — see §8. An untested backup is a hypothesis.

---

## 8. Restore the database

### Drill (do this at least once, before you need it)

Restore into a scratch database and confirm the data is really there:

```bash
# 1. Create a throwaway Neon branch or a local database.
createdb tekaplay_restore_test

# 2. Restore the archive into it.
pg_restore --clean --if-exists --no-owner \
  -d postgresql://localhost/tekaplay_restore_test \
  backups/tekaplay-20260816T101500Z.dump

# 3. Prove the customer data survived — not just that the command exited 0.
psql postgresql://localhost/tekaplay_restore_test -c \
  "SELECT (SELECT count(*) FROM users)         AS users,
          (SELECT count(*) FROM organizations) AS orgs,
          (SELECT count(*) FROM game_sessions) AS sessions;"

# 4. Clean up.
dropdb tekaplay_restore_test
```

If step 3 returns numbers that match production, the backup is real.

### Real restore, production

1. **Stop writes.** Render dashboard → `tekaplay-api` → **Suspend**. Restoring
   underneath a live application produces a half-old, half-new database.
2. **Back up the current state anyway**, however broken. You cannot undo a
   restore, and the corrupt database may still hold data the backup does not.
3. Restore:
   ```bash
   pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" <archive>.dump
   ```
4. Confirm the schema version matches the code: `alembic current`.
5. Resume the service and check `/health/ready`.

For accidental damage in the last few hours, Neon's point-in-time restore is
faster and safer than a dump: Neon console → *Branches* → restore to a
timestamp. It creates a branch, so the damaged database is not overwritten
while you check.

---

## 9. Create a new production database

For a second environment, a provider migration, or a fresh start.

```bash
# 1. Create the database (Neon console, or any PostgreSQL 16 server).
#    Nothing about the schema is Neon-specific.

# 2. Point at it and create the schema.
cd backend
export DATABASE_URL='postgresql://…?sslmode=require'
alembic upgrade head

# 3. Verify.
alembic current          # should print the latest revision

# 4. Create the first admin.
python -m app.scripts.create_admin you@example.com 'a-long-password'

# 5. Optional: load the demo mission content.
python -m app.scripts.seed_demo
```

To copy existing data instead of starting empty, restore a dump (§8) rather
than running migrations — the dump already contains the schema.

---

## 10. Rotate secrets

| Secret | Effect of rotating | Steps |
|---|---|---|
| `SECRET_KEY` | **Every user is logged out immediately.** Access tokens are signed with it; refresh tokens are opaque and stored hashed, so sessions cannot be recovered. | Generate `openssl rand -hex 32`, set it on `tekaplay-api`, save. Do it at a quiet hour. |
| `DATABASE_URL` | Brief downtime while the service restarts. | Reset the password in the Neon console, update the variable, save. |
| `REDIS_URL` | Rate-limit counters and the AI cache reset. Harmless — the limiter fails open. | Rotate in Upstash, update the variable. |
| `STRIPE_API_KEY` | Payments fail until updated. | Roll the key in Stripe, update, then revoke the old one — in that order. |
| `STRIPE_WEBHOOK_SECRET` | Webhooks are rejected until updated, so subscription state silently stops syncing. Stripe retries for days, so a short gap self-heals. | Roll in the Stripe dashboard, update immediately. |
| `AI_API_KEY` | AI drafting fails. | Roll in the Anthropic console, update, revoke the old. |
| `SMTP_PASSWORD` | Outgoing email fails. | Roll with the email provider, update. |

Rotate on a fixed schedule, and always immediately if a key was pasted into a
chat, a log, a screenshot, or a commit.

**If a secret reached Git:** rotating it is the fix. Rewriting history is not —
the commit is already cloned, cached, and indexed. Rotate first, then clean up.

---

## 11. Troubleshoot a failed deployment

Work down this list; it is roughly ordered by likelihood.

### The service will not start

Read the logs (§5) from the **top** of the deploy, not the bottom.

| Log says | Cause | Fix |
|---|---|---|
| `Invalid production configuration:` followed by a list | The startup guardrail rejected the config — this is working as designed | Fix exactly what it lists. Each line names the variable. |
| `SECRET_KEY is still the built-in development value` | `SECRET_KEY` unset | Set it. `openssl rand -hex 32` |
| `CORS_ORIGINS must not contain '*'` | Wildcard origin with credentialed requests | Set the real origin as a JSON array |
| `DATABASE_URL points at localhost` | Copied the local value | Paste the Neon connection string |
| `invalid literal for...` / JSON decode error on boot | `CORS_ORIGINS` is not a JSON array | `["https://…"]`, with the brackets |
| `connect() got an unexpected keyword argument 'sslmode'` | An older build without `app/db/url.py` | Deploy current `main` |
| `password authentication failed` | Wrong credentials, or the password was rotated | Re-copy the connection string from Neon |
| `relation "users" does not exist` | Migrations never ran | §3 |

### The build fails

| Symptom | Fix |
|---|---|
| `npm ci` errors about the lockfile | `package.json` and `package-lock.json` are out of sync. Run `npm install` locally and commit the lockfile. |
| Frontend build succeeds but the app calls `localhost:8000` | `NEXT_PUBLIC_API_BASE_URL` was not set **as a build argument**. See §4. |
| Docker build cannot find a file | Check `backend/.dockerignore` / `frontend/.dockerignore` — the build context excludes more than you think. |

### The API is up but the browser app is broken

Almost always CORS. Open the browser console; if you see
*"blocked by CORS policy"*:

- `CORS_ORIGINS` on the API must contain the web origin **exactly** — scheme
  included, no trailing slash. `https://tekaplay-web.onrender.com`, not
  `tekaplay-web.onrender.com` and not `…onrender.com/`.
- The API works fine under `curl` in this state, which is what makes it
  confusing. `curl` does not enforce CORS; browsers do.

### Everything is just slow, once

Free-plan cold start (§6). Expected. The fix is a paid plan, not a code change.

### Nothing above matches

```bash
python -m app.scripts.check_env    # in a Render shell, or locally with prod env
```

It prints what the application actually resolved, with credentials redacted —
which is usually the fastest way to find the variable you thought you set.
