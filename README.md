# Fasla web app

Accounts, brands, Excel template download/upload, the planner, and results, all on Supabase (Auth, Postgres, Storage).

```
browser ──(Supabase Auth: email/password, optional Google)──> Supabase
   │  access token (JWT)
   ▼
FastAPI (this repo) ── checks the JWT ── Postgres tables brands, plans (RLS on)
   │                                  └─ Storage bucket fasla-files (private)
   └─ runs the planner on a background thread, saves the report, the page polls
```

Every API query is filtered by the signed-in user's id, and the database has row-level security as a second lock, so one account can never see another account's brands, plans or files.

---

## Put it online (about 15–20 minutes)

### 1. Create the Supabase project
1. Go to supabase.com → **New project**. Pick region **Frankfurt (eu-central-1)**, the closest to Egypt. Save the database password somewhere safe.
2. When it's ready, open **SQL Editor → New query**, paste all of `supabase/migrations/0001_fasla_init.sql`, and press **Run**. It creates the tables, the security rules and the private `fasla-files` bucket. Running it twice is safe.

### 2. Copy the keys
- **Project Settings → API keys**: copy the `anon` (publishable) key and the `service_role` (secret) key.
- **Project URL**: `https://<ref>.supabase.co` (shown on the same page).
- **Connect** button (top bar) → **Session pooler** → copy the URI and put your database password in it. This is `DATABASE_URL`.
- Newer projects sign logins with public keys; nothing more is needed. If your project still uses the **legacy JWT secret**, copy it from **Project Settings → JWT Keys** into `SUPABASE_JWT_SECRET`.

### 3. Deploy on Render
1. Put this folder in a GitHub repo (private is fine).
2. render.com → **New → Blueprint** → choose the repo. It reads `render.yaml`.
3. Fill in `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL` (and `SUPABASE_JWT_SECRET` only if step 2 said so). Deploy.
4. Open `https://<your-app>.onrender.com/healthz`. It should say `"ok":true` with an empty `config_problems` list.

Use the **Starter** plan or better: a plan takes 30–90 s of CPU, and the free plan sleeps after 15 minutes idle. Any Docker host works (Railway, Fly.io, a VPS); the same environment variables apply.

### 4. Tell Supabase where the app lives
**Authentication → URL Configuration**:
- **Site URL**: `https://<your-app>.onrender.com`
- **Redirect URLs**: add `https://<your-app>.onrender.com/**`

Without this, the "confirm your email" and "reset password" links point to localhost.

**Authentication → Sign In / Providers → Email**: keep **Confirm email** on. Supabase's built-in email sender is limited to a few emails per hour, so before launch add your own SMTP (Authentication → Emails → SMTP; Resend or Brevo have free tiers). You can also edit the email templates there (for example add Arabic).

### 5. Optional: "Continue with Google"
1. Google Cloud Console → APIs & Services → Credentials → **OAuth client ID** (Web). Authorized redirect URI: `https://<ref>.supabase.co/auth/v1/callback`.
2. Supabase → Authentication → Providers → **Google**: paste the client ID and secret, enable.
3. On Render set `GOOGLE_LOGIN=true` and redeploy. The button appears on the sign-in page.

---

## Run it on your laptop

No Supabase needed: local accounts, SQLite and files on disk.

```bash
pip install -r requirements.txt
cp .env.example .env        # then set AUTH_MODE=dev, STORAGE_MODE=local, DATABASE_URL=sqlite:///data/fasla.db
uvicorn fasla.main:app --reload --port 8000
```
Open http://localhost:8000. The app refuses to start if `AUTH_MODE=dev` is combined with Supabase keys, so local test logins can't reach production by accident.

To run against your real Supabase project locally, keep `.env` with the Supabase values and add `http://localhost:8000/**` to the Redirect URLs.

## Tests

```bash
pip install -r requirements-dev.txt && playwright install chromium
pytest tests                                   # API (SQLite), Supabase JWT + Storage adapters (mocked)
pytest tests   # the Postgres and RLS tests also run when a local Postgres is on port 54329 (see tests/), otherwise they skip
# browser end-to-end (app must be running in dev mode on :8077)
python tests/e2e/run_e2e.py http://localhost:8077 /tmp/shots
```

What was tested before handover: 16 API tests on SQLite and on PostgreSQL 16 using the real migration; 8 row-level-security tests; 10 tests of the Supabase JWT checks (new public-key logins, key rotation, legacy secret, expired/forged tokens) and the Storage calls; 11 planner tests; a browser run through sign-up → brand → template download → bad file → upload → settings → plan → results → report download → Arabic → second account sees nothing → phone layout. The real Supabase servers were not reachable from where this was built, so the first live sign-up is the last check: do one yourself after step 4.

## The workbook

| Sheet | Columns |
|---|---|
| Products | style_id, name, category, color, size, unit_cost, price |
| Orders | date, style_id, color, size, qty, zone, courier, status (delivered / returned / cancelled) |
| Production | season, style_id, color, size, produced, (season_start optional) |
| Couriers | courier, zone, fee, return_fee |
| Settings | setting, value |
| OwnerEstimates | style_id, estimate (optional) |

Any sizes work (S–XXL, 36–46, one size). Seasons are matched by `season_start` dates if given, otherwise by the year in the season name. Upload errors are listed in plain language with the sheet and row.

## API (all need `Authorization: Bearer <supabase access token>`)

| | |
|---|---|
| `GET /api/templates/empty`, `/api/templates/example` | download templates (no login needed) |
| `GET/POST /api/brands`, `PATCH/DELETE /api/brands/{id}` | brands (max 20 per account) |
| `POST /api/brands/{id}/uploads` | multipart `file` → checks the workbook, creates a draft plan |
| `GET /api/brands/{id}/plans`, `GET /api/plans/{id}` | list / one plan (with results when done) |
| `POST /api/plans/{id}/run` | `{"settings": {...}}` starts planning |
| `GET /api/plans/{id}/report`, `/input`, `DELETE /api/plans/{id}` | files and delete |

## Files
- `fasla/`: web server (`main.py`), login checks (`auth.py`), storage (`storage.py`), workbook reading and planning (`planner.py`), database (`db.py`), settings (`config.py`)
- `brandopt/`: the planning engine (forecast, optimisation, couriers, cash flow, Excel report)
- `web/index.html`: the app; `web/results.html`: the results view shown inside it
- `supabase/migrations/`: database and storage setup

## Limits to know
- Plans run inside the web process (`PLAN_WORKERS` at a time, one running plan per account). If the server restarts mid-plan, that plan is marked failed and can be re-run with one click. For many simultaneous users, move planning to a separate worker.
- Run one server instance (Render default). Two instances would each run their own queue, which still works but wastes CPU.
