# Deploying to Vercel

This repository can run entirely on Vercel's serverless Python runtime. The steps below assume you already have the infrastructure (managed Postgres, Redis, SMTP provider, OpenAI key, etc.) available on the public internet.

## 1. Prerequisites

- Vercel account with the [Vercel CLI](https://vercel.com/docs/cli) installed (`npm i -g vercel`).
- A reachable Postgres instance; Vercel cannot reach your local database. Services such as Neon, Supabase, RDS, or Timescale work well.
- Optional but recommended: hosted Redis for caching/rate limiting if you rely on `REDIS_URL`.

## 2. Project structure changes

- `vercel.json` instructs Vercel to build `api/index.py` with the Python 3.11 runtime and route every request through it.
- `api/index.py` imports `app.main:app` and exposes a `handler = Mangum(app)` entry point so Vercel can execute the FastAPI application as a serverless function.

## 3. Configure environment variables

In your project directory run:

```bash
vercel env add ENV production
vercel env add DATABASE_URL
vercel env add OPENAI_API_KEY
vercel env add APP_BASE_URL https://<your-vercel-domain>
vercel env add SECRET_KEY
vercel env add SESSION_COOKIE_NAME chatbot_session
vercel env add SESSION_EXPIRE_MINUTES 1440
vercel env add OPENAI_CHAT_MODEL gpt-4o-mini
vercel env add OPENAI_EMBEDDING_MODEL text-embedding-3-large
vercel env add SMTP_HOST
vercel env add SMTP_PORT
vercel env add SMTP_USERNAME
vercel env add SMTP_PASSWORD
vercel env add SMTP_FROM_EMAIL
vercel env add SMTP_USE_TLS true
vercel env add CORS_ALLOW_ORIGINS https://<your-frontend-domain>
vercel env add REDIS_URL
```

Set each value to the production secret you want Vercel to use. Repeat the command for every variable listed in `.env` (or use `vercel env add --environment=production ...`).

## 4. First deployment

```bash
# Authenticate once
vercel login

# Link the repository (creates or selects a Vercel project)
vercel link

# Deploy preview to verify build output
vercel

# Promote to production when ready
vercel --prod
```

Vercel installs the dependencies declared in `requirements.txt` (including `mangum`) and builds the serverless function automatically.

## 5. Database migrations & health checks

1. **Vercel serverless auto-migrations** – `api/index.py` runs `alembic upgrade head` once per cold start before the FastAPI app is imported. Every deployment (or cold boot) therefore migrates Neon automatically, provided all `alembic/versions/*.py` files are committed.
2. **Containers / workers** – `scripts/runserver.py` runs the same Alembic command whenever `RUN_DB_MIGRATIONS=1`. Keep that env var enabled in Docker Compose, ECS, etc., so restarts always apply pending migrations before Gunicorn starts.
3. **Neon branch hygiene** – when cloning or resetting a branch, run `alembic upgrade head` (or start the container once with `RUN_DB_MIGRATIONS=1`) *before* updating Vercel's `DATABASE_URL`. Avoid manual `DROP SCHEMA` unless you immediately reapply migrations.
4. **CI/CD smoke test** – run `python scripts/smoke_test.py` (or add the command to GitHub Actions) to verify that:
	- Alembic knows about the current revision (`alembic current`)
	- The `projects` table exists and responds to a simple `SELECT`

The smoke test uses whatever `DATABASE_URL` is in the environment, so point it at the branch you are about to promote.

## 6. Post-deploy checks

1. Hit `/api/health` (if you add such an endpoint) or `/api/chat/...` to confirm the API responds.
2. Test the admin UI at `https://<your-vercel-domain>/admin` and confirm static assets load.
3. Monitor the Vercel logs: `vercel logs <deployment-url> --since 1h`.

With these steps the full FastAPI application—routers, templates, and static files—runs on Vercel's serverless infrastructure.

## 7. Neon branch hygiene

- Avoid manual `DROP SCHEMA` or full branch resets in production unless you immediately re-run all migrations.
- Keep a naming convention for temporary branches so it's obvious which ones are safe to delete or reset.
- Before promoting a new branch or failover, run `alembic upgrade head` against it and run the smoke test mentioned above (`python scripts/smoke_test.py`).
