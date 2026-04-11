# GioMeds 💊

Medication administration tracking system for Gio the dog.

Sends SMS reminders to two caregivers at scheduled medication times, accepts confirmation replies, cross-notifies the other person when a dose is confirmed, and logs everything to a shared Google Sheet. Includes a password-protected admin web UI for managing the schedule.

---

## Setup Guide

### 1. Clone and install dependencies

```bash
git clone https://github.com/YOUR_USERNAME/giomeds.git
cd giomeds
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Twilio

1. Create a [Twilio account](https://www.twilio.com) and purchase a phone number with SMS capability.
2. Note your **Account SID**, **Auth Token**, and the **phone number** (E.164 format, e.g. `+14155551234`).
3. After deploying (step 5), set your webhook URL in the Twilio console:
   - Go to **Phone Numbers → Manage → Active Numbers → your number**
   - Under **Messaging → A message comes in**, set:
     - **Webhook**: `https://your-app.railway.app/sms/incoming`
     - **HTTP Method**: `HTTP POST`

### 3. Configure Google Sheets

1. Go to [Google Cloud Console](https://console.cloud.google.com) and create a new project (or use an existing one).
2. Enable the **Google Sheets API** and **Google Drive API** for your project.
3. Create a **Service Account**:
   - IAM & Admin → Service Accounts → Create Service Account
   - Download the JSON key file
4. Create a Google Sheet and note the **Sheet ID** from the URL:  
   `https://docs.google.com/spreadsheets/d/SHEET_ID_HERE/edit`
5. Share the sheet with the service account email (found in the JSON key file, e.g. `giomeds@your-project.iam.gserviceaccount.com`), granting **Editor** access.
6. Collapse the JSON key file contents to a single line (no literal newlines) — you'll paste this into `GOOGLE_SERVICE_ACCOUNT_JSON`.

   Quick way to single-line a JSON file:
   ```bash
   python -c "import json,sys; print(json.dumps(json.load(open('key.json'))))"
   ```

### 4. Set environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in all values. See `.env.example` for descriptions of each variable.

**Important:** Never commit `.env` to version control.

### 5. Deploy to Railway

**Option A — Railway CLI:**
```bash
npm install -g @railway/cli
railway login
railway init          # link to a new project
railway up            # deploy
```

**Option B — Railway dashboard:**
1. Push this repo to GitHub.
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo → select `giomeds`.
3. Railway will auto-detect the `Procfile` and deploy.

**Set environment variables on Railway:**
- Dashboard → your service → Variables
- Add every variable from `.env.example` with your real values.

**Persistent storage for `schedule.json`:**
Railway's filesystem is ephemeral between deploys. To persist the schedule, add a Railway **Volume** mounted at `/app` (or the repo root). Alternatively, set your schedule once via the admin UI right after each deploy — the default schedule (`08:00` / `20:00`) is always used as a fallback if the file is missing.

### 6. Access the admin UI

Navigate to `https://your-app.railway.app/admin` and log in with your `ADMIN_PASSWORD`.

- **Dashboard** — shows the current dose window status and the last 10 log entries.
- **Edit Schedule** — change the medication name, dose times, and timezone. Changes take effect immediately without restarting.

---

## SMS Reply Reference

| Reply | Effect |
|---|---|
| `YES` | Confirm dose, time = now |
| `YES 7:45` | Confirm, administered at 7:45 (AM/PM inferred) |
| `YES 7:45pm` | Confirm, administered at 7:45 PM |
| `YES gave with food` | Confirm, time = now, note logged |
| `YES 7:45 gave with food` | Confirm with custom time and note |
| `NO` or `N` or `SKIP` | Mark dose as skipped |

---

## Architecture

```
app.py          Flask app: SMS webhook + admin UI
scheduler.py    APScheduler: daily dose reminders + follow-ups + missed-dose alerts
sms.py          Twilio send helpers + message templates
sheets.py       Google Sheets read/write
state.py        Thread-safe in-memory dose state
parser.py       Incoming SMS reply parser
config.py       Environment variable loading/validation
schedule.json   Current medication schedule (written by admin UI)
```

### Scheduling behaviour

- At each scheduled dose time: sends initial reminder to both users, schedules a follow-up in 30 minutes.
- Every 30 minutes if unconfirmed: sends a follow-up reminder, schedules the next one.
- When the next dose time arrives: if previous dose is still pending, marks it as **missed**, logs it, and alerts both users before starting the new window.
- Single Gunicorn worker ensures only one scheduler instance runs.

---

## Local development

```bash
source .venv/bin/activate
flask --app app run --debug
```

To test the SMS webhook locally, use [ngrok](https://ngrok.com):
```bash
ngrok http 5000
# Then set your Twilio webhook to: https://xxxx.ngrok.io/sms/incoming
```
