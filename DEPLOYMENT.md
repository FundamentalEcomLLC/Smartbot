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

## 5. Database migrations

Vercel's serverless runtime is ephemeral, so run Alembic migrations from your local machine or CI:

```bash
alembic upgrade head
```

The command must run against the same `DATABASE_URL` you configured for Vercel.

## 6. Post-deploy checks

1. Hit `/api/health` (if you add such an endpoint) or `/api/chat/...` to confirm the API responds.
2. Test the admin UI at `https://<your-vercel-domain>/admin` and confirm static assets load.
3. Monitor the Vercel logs: `vercel logs <deployment-url> --since 1h`.

With these steps the full FastAPI application—routers, templates, and static files—runs on Vercel's serverless infrastructure.
