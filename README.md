[README.md](https://github.com/user-attachments/files/34442359/README.md)
# GEOPOLITICS

Single-player, browser-based strategy game. Command one of ten nations on a real world map against nine
strategic computer opponents; own all ten countries to win. Flask + MongoDB (Atlas), vanilla JS, AI battle commentary.

Screens: **War Room** (map + tactical grid, commands, live event feed), **Headquarters** (saved campaigns),
**Intelligence** (strength comparison, ten-nation status, feed with NPC rationale, battle replay),
**Operations Manual**, plus new-campaign, notification centre and admin console.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                      # edit values
python run.py                                             # http://localhost:5000
python -m pytest -q                                       # 79 tests
```
With `MONGO_URI` empty an in-memory database is used (development/tests only). Set it to your Atlas URI for real use.

## Environment variables
| Variable | Purpose |
|---|---|
| `SECRET_KEY` | session signing (required in production) |
| `MONGO_URI`, `MONGO_DB_NAME` | MongoDB Atlas connection |
| `PUBLIC_BASE_URL` | absolute links used in e-mails |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | AI commentary via Anthropic (preferred when set), server side only |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | AI commentary via Google Gemini (AI Studio free tier), used when Anthropic is not set |
| `RESEND_API_KEY`, `EMAIL_FROM` | transactional e-mail via Resend (sender domain must be verified in Resend) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` | alternative e-mail path (e.g. Gmail App Password), used when `RESEND_API_KEY` is empty |
| `CRON_SECRET` | bearer secret protecting `/api/cron/process-emails` (Vercel Cron Job) |
| `ADMIN_EMAIL`, `ADMIN_ALERTS_ENABLED`, `ADMIN_ALERT_EMAIL` | admin identity hint and opt-in alerts |
| `COOKIE_SECURE` | `1` on HTTPS |

No credential is stored in the repository or sent to the browser.

## Deploy (Render + MongoDB Atlas)
1. **Atlas**: create a cluster and database user, allow Render's network access, copy the connection string.
   To give reviewers access, add their accounts under *Database Access* (read-only role on the `geopolitics` database) – never share the admin password.
2. **Render**: new Web Service from this repo (uses `render.yaml`); set the secret variables above.
3. Run once from the Render shell: `flask --app run db-status` (prints live connection evidence: server version and collections).
4. Reviewer + administrator accounts (trusted operator step, run once against the live database):
   `flask --app run seed-accounts --from-csv seed/reviewer_accounts.csv --admin milan.sewdass@gmail.com`
   The CSV (`email,password`, git-ignored, never commit it) holds pre-issued passwords; omit `--from-csv` and list
   e-mails instead to have strong passwords generated and printed once. Accounts are created verified; only the
   `--admin` address gets the admin role. Re-running is safe (existing accounts are left alone unless `--reset-password`).
   Users can sign in with username or e-mail, and should change the issued password via *Forgot password*.
5. Alternative admin route: sign up with `milan.sewdass@gmail.com`, click the e-mail confirmation link, then run once
   `flask --app run grant-admin milan.sewdass@gmail.com`. (If e-mail is not configured yet, confirm ownership out-of-band and use `flask --app run verify-email <address>` first.) Admin status can never be obtained from the sign-up form.
6. Failed e-mails are retried (limited attempts). Schedule `flask --app run process-emails` as a cron job, or use *Process due jobs* in the admin console, to deliver anything still waiting.

## Rules (as implemented and tested)
* Chosen nation + 9 random distinct nations; random turn order; soldiers from the population table (inclusive upper bounds); population source and retrieval date recorded in `app/data/`.
* Round 1: no reinforcements. From round 2, 2 soldiers per controlled country at the start of each turn.
* One attack per turn or skip. Attacker needs ≥ 2 soldiers; each phase both sides roll `min(attacker−1, defender)` dice (no cap), paired in rolled order, higher die wins, ties no casualty.
* Conquest: owner changes, `max(2, ⌊original value ÷ 2⌋)` soldiers, once; the new country can launch from the next turn.
* Transfers only between own countries, never during an attack, origin keeps ≥ 1.
* Stop Attack ends the turn. Eliminated nations leave the rotation. Whoever owns all ten wins (NPC victory is handled).

## Architecture
```
app/models.py    Territory, Player, Game (domain classes, serialisation, public-safe projection)
app/engine.py    GameEngine + BattleEngine: all rules, no I/O; emits persisted events with before/after snapshots
app/npc.py       StrategicNPC (one documented strategy) · app/odds.py exact win probability (dynamic programming)
app/commentary.py AI narration from verified battle facts: one live line per phase while a battle is in progress, a full recap when it ends; labelled fallback; never touches state
app/db.py        Database + repositories (users, games, events, battles, notification_prefs, email_jobs, tokens, audit_log, error_log)
app/auth.py      accounts, e-mail verification, reset, CSRF, rate limiting, role checks
app/routes.py    pages + JSON API · app/admin.py admin console API · app/emailer.py e-mail queue/provider
app/static/js/   map.js (SVG world map), game.js (War Room), intelligence.js, headquarters.js, new.js, notifications.js, admin.js
```
Server-authoritative: the browser sends intents with the game `version`; the server validates ownership, turn, soldier invariants and applies the change atomically with an optimistic-concurrency check (`update_one({_id, version}, $set)`; the `commentary` array is excluded from that write so background narration is never overwritten). A duplicate/stale request gets `409` and changes nothing. Events and battles have unique `(game_id, seq)` / `(game_id, battle_no)` indexes so retries cannot double-insert.

### Database (MongoDB)
`users` (unique username/e-mail, role, verified, lock state) · `games` (state, owner, unique `share_code`, `version`; indexes owner+updated, status+updated) · `events` (full ordered history incl. NPC rationale; unique game+seq) · `battles` (every phase, dice, outcome, commentary; unique game+battle_no) · `notification_prefs` · `email_jobs` (dedupe key, status, attempts) · `tokens` (hashed) · `audit_log` · `error_log`.
Backup/recovery: enable Atlas continuous/scheduled backups; restore into a new cluster and point `MONGO_URI` at it. No migrations are required (documents carry defaults on read).

### Security
Server-side authentication and authorisation on every write; owners only; spectators use `/watch/<code>` and `/api/watch/...` (no account, read-only, public-safe projection: no user ids, names or e-mails). Admin role lives only in the database, is granted by a trusted CLI command for a verified e-mail, is checked on every request and every admin action is audited. Password hashing, CSRF tokens, lockout and per-IP rate limits, strict CSP, text-only DOM rendering. If the e-mail provider or AI is unavailable the game keeps working and failures are logged/shown honestly.

## Testing
`python -m pytest -q` (76 tests): population boundaries; setup/random order; dice (worked 10v2 example, 2v5, 3v5, ties, no cap); conquest rewards (base 1 → 2, base 9 → 4, once); launch lock; reinforcement timing; transfers; skip/stop; elimination/victory; NPC reasons, odds, 8 full NPC-only games; API auth/CSRF/lockout/rate limit; version idempotency; ownership isolation; anonymous spectator read-only and public-safe; persistence/replay data; AI success/failure/fallback with key never leaked; admin role rules; notifications/unsubscribe.

## Not verified in this repository's test environment
Live MongoDB Atlas connection, a real AI call, a delivered e-mail and the public deployment need your credentials/accounts; run the steps above and `flask --app run db-status`.

## Deploy on Vercel
* Entrypoint: `index.py` exposes the Flask `app`; Vercel's Python runtime auto-detects it (`pyproject.toml` also
  declares the entrypoint). Python version is pinned to 3.12 by `.python-version`.
* Static files live in `app/static/` and are served by Flask at `/static/...` (inside the Python package so
  the serverless build always bundles them).
* On Vercel (`VERCEL=1`) commentary and e-mail run inside the request (serverless functions cannot keep background
  threads); `vercel.json` allows 60 s per request.

### 1. Create the MongoDB Atlas database (free tier is fine)
1. Create an account at https://cloud.mongodb.com, choose the **free M0 cluster**, pick a region near you, click *Create*.
2. *Database Access* → *Add New Database User*: username + password. Choose **Password** (not certificates). Copy the password somewhere safe.
3. *Network Access* → *Add IP Address* → **Allow access from anywhere** (`0.0.0.0/0`) — Vercel has no fixed outbound IPs.
4. *Overview* → *Connect* → *Drivers* → *Python 3.6 or later* → copy the connection string, replace
   `<password>` (URL-encode special characters, e.g. `@` → `%40`) and append `&appName=geopolitics`. That string is `MONGO_URI`.

### 2. Create the Resend e-mail setup
1. Sign up at https://resend.com and create an API key (*API Keys* → *Create API Key*) — that is `RESEND_API_KEY`.
2. Under *Domains*, verify your sending domain (add the DNS records Resend shows), then use
   `EMAIL_FROM=GEOPOLITICS <no-reply@yourdomain.com>`.
   For quick personal testing before DNS is set up you can use `EMAIL_FROM=GEOPOLITICS <onboarding@resend.dev>`,
   which only delivers to the e-mail address of your Resend account.
3. Test from your computer with `.env` filled in: `flask --app run test-email you@example.com`.

### 3. Push to GitHub and import into Vercel
1. Create a private/public repository on GitHub, push this folder to it
   (`git init`, `git add .`, `git commit`, `git remote add origin ...`, `git push -u origin main`).
2. vercel.com → *Add New Project* → import the GitHub repository. Vercel detects the Python/Flask app automatically.
3. Project → *Settings* → *Environment Variables* (Production + Preview + Development), then redeploy:

   | Variable | Value |
   |---|---|
   | `MONGO_URI` | your Atlas connection string (required — the app refuses to start on Vercel without it) |
   | `MONGO_DB_NAME` | `geopolitics` |
   | `SECRET_KEY` | long random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `PUBLIC_BASE_URL` | `https://your-project.vercel.app` (no trailing slash) |
   | `COOKIE_SECURE` | `1` |
   | `RESEND_API_KEY` | your Resend key |
   | `EMAIL_FROM` | `GEOPOLITICS <no-reply@yourdomain.com>` |
   | `CRON_SECRET` | another random string for the e-mail cron job |
   | `ANTHROPIC_API_KEY` | optional, AI commentary |

4. After the first successful deploy check `https://your-project.vercel.app/healthz` → `{"status":"ok"}`.
   If it says `MONGO_URI is not set`, the environment variables were not applied before the build — redeploy.

### 4. E-mail delivery on Vercel
* `VERCEL=1` turns on `EMAIL_SYNC`: verification, reset and notification e-mails are sent inside the request that
  queues them (no background threads). If the provider is temporarily down the job stays in the queue and is retried
  with back-off.
* A Vercel Cron Job calls `POST /api/cron/process-emails` daily at 01:00 UTC (declared in `vercel.json`) to deliver
  anything still due; it authenticates with `Authorization: Bearer <CRON_SECRET>`. The *Process due jobs* button in
  the admin console does the same on demand. On the Hobby plan Vercel allows at most one cron run per day.
* Failed e-mails are retried automatically on subsequent sweeps (limited attempts), and every job is visible under
  Admin → E-mail jobs.

### 5. Provision your account (one-time, from your computer)
Vercel has no shell, so trusted commands run locally with `MONGO_URI` pointing at Atlas:

```bash
flask --app run db-status                                 # proves the Atlas connection works
flask --app run seed-accounts --admin you@example.com     # creates verified owner + admin account, prints a password once
flask --app run grant-admin you@example.com               # alternative: after normal sign-up + e-mail verification
```

* `.vercelignore` keeps `.env`, `env.txt`, CSVs, tests and tools out of the upload; `.gitignore` does the same for git.
* Atlas → Network Access → `0.0.0.0/0` is required as above.
