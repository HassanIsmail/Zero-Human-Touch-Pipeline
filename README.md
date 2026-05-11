# Zero Human Touch Pipeline

A fully automated, end-to-end software delivery pipeline. It polls Jira for stories labelled `ai-ready`, builds a complete web application using Claude AI, runs automated tests, opens a GitHub PR, deploys to Vercel, runs Playwright QA, emails the report, and closes the Jira story — all without human intervention.

---

## How It Works

```
Jira (ai-ready story)
        │
        ▼
Stage 1 — Download requirements.md + transition to In Progress
        │
        ▼
Stage 2 — Claude AI builds the web app (index.html, app.js, style.css, vercel.json)
        │
        ▼
Stage 3 — Jest unit tests (auto-fix loop, up to 3 iterations)
        │
        ▼
Stage 4 — Push to GitHub + open Pull Request
        │
        ▼
Stage 5 — Deploy to Vercel (preview) + health check
        │
        ▼
Stage 6 — Playwright QA against the live URL
        │
        ▼
Stage 7 — Email QA report with screenshots
        │
        ▼
Stage 8 — Transition Jira story to Done (or Bug Reported on failure)
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.8 or higher |
| Node.js + npm | Any LTS release |
| Claude Code CLI | Installed and logged in (`claude` on PATH) |

> **Claude Code must be authenticated** before running the pipeline. Run `claude` once interactively to log in. The pipeline calls `claude -p` (non-interactive mode) and inherits your session — no `ANTHROPIC_API_KEY` is needed.

---

## Installation

**1. Clone the repository**

```bash
git clone https://github.com/your-org/Zero-Human-Touch-Pipeline.git
cd Zero-Human-Touch-Pipeline
```

**2. Run the setup script**

```bash
chmod +x setup.sh
./setup.sh
```

This will:
- Create a Python virtual environment in `./venv/`
- Install all Python dependencies from `requirements.txt`
- Install the Playwright Chromium browser
- Verify Node.js and npm are available

**3. Configure environment variables**

Copy the example file and fill in your credentials:

```bash
cp .env.example .env
```

Open `.env` and set the following values:

```env
# Jira
JIRA_URL=https://yourorg.atlassian.net
JIRA_EMAIL=you@yourorg.com
JIRA_API_TOKEN=your_jira_api_token
JIRA_PROJECT_KEY=ZHTP

# GitHub
GITHUB_TOKEN=ghp_your_github_personal_access_token
GITHUB_REPO=your-org/your-repo

# Vercel
VERCEL_TOKEN=your_vercel_token
VERCEL_PROJECT_ID=prj_xxxxxxxxxxxxxxxx
VERCEL_ORG_ID=team_xxxxxxxxxxxxxxxx
VERCEL_PROJECT_NAME=your-vercel-project-name

# Email (SMTP / STARTTLS)
EMAIL_FROM=pipeline@yourorg.com
EMAIL_TO=team@yourorg.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=pipeline@yourorg.com
SMTP_PASSWORD=your_smtp_password
```

---

## Running the Pipeline

**Activate the virtual environment and start:**

```bash
source venv/bin/activate
python main.py
```

The pipeline will:
1. Run immediately on startup
2. Poll Jira every **5 minutes** for new `ai-ready` stories
3. Process each story through all 8 stages automatically
4. Log everything to `logs/pipeline-YYYYMMDD.log`

**Stop the pipeline:**

```bash
Ctrl+C
```

---

## Jira Story Setup

For a story to be picked up by the pipeline it must:

1. Belong to the configured Jira project (e.g. `ZHTP`)
2. Have the label **`ai-ready`**
3. Be in **`To Do`** status
4. Have a file attachment named exactly **`requirements.md`**

The `requirements.md` attachment is what Claude reads to build the application. It should describe the app's features, UI, and acceptance criteria in plain English.

---

## Project Structure

```
Zero-Human-Touch-Pipeline/
├── main.py                  # Entry point — orchestrator + scheduler
├── requirements.txt         # Python dependencies
├── setup.sh                 # One-shot environment setup script
├── .env                     # Your credentials (not committed)
├── pipeline/
│   ├── builder.py           # Stage 2 — Claude AI app generation
│   ├── test_runner.py       # Stage 3 — Jest test generation + execution
│   ├── github_client.py     # Stage 4 — Git push + PR creation
│   ├── vercel_client.py     # Stage 5 — Vercel deployment + health check
│   ├── qa_agent.py          # Stage 6 — Playwright QA + bug report
│   ├── email_client.py      # Stage 7 — SMTP email delivery
│   ├── jira_client.py       # Stages 1 & 8 — Jira transitions + comments
│   └── logger.py            # Shared rotating file + console logger
├── workspace/               # Per-story working directories (auto-created)
│   └── ZHTP-42/
│       ├── app/             # Generated app files
│       ├── bug-report.md    # Playwright QA report
│       └── screenshots/     # QA screenshots
└── logs/                    # Daily rotating log files
```

---

## Environment Variable Reference

| Variable | Required | Description |
|---|---|---|
| `JIRA_URL` | Yes | Your Atlassian instance URL, e.g. `https://yourorg.atlassian.net` |
| `JIRA_EMAIL` | Yes | Atlassian account email |
| `JIRA_API_TOKEN` | Yes | API token from [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `JIRA_PROJECT_KEY` | Yes | Jira project key, e.g. `ZHTP` |
| `GITHUB_TOKEN` | Yes | Personal access token with `repo` scope |
| `GITHUB_REPO` | Yes | `owner/repo` format, e.g. `acme/my-app` |
| `VERCEL_TOKEN` | Yes | Vercel personal access token |
| `VERCEL_PROJECT_ID` | Yes | Vercel project ID (`prj_…`) |
| `VERCEL_ORG_ID` | No | Vercel team/org ID (`team_…`) — leave blank for personal accounts |
| `VERCEL_PROJECT_NAME` | Yes | Human-readable Vercel project name |
| `EMAIL_FROM` | Yes | Sender email address |
| `EMAIL_TO` | Yes | Recipient address(es), comma-separated |
| `SMTP_HOST` | Yes | SMTP server hostname |
| `SMTP_PORT` | No | SMTP port (default: `587`) |
| `SMTP_USER` | Yes | SMTP login username |
| `SMTP_PASSWORD` | Yes | SMTP login password |

---

## Logs

Logs are written to `logs/pipeline-YYYYMMDD.log` and also printed to the console. Each stage logs its progress, and any errors include a full traceback. On failure, the story is commented on in Jira and returned to `To Do` for automatic retry on the next poll.

---

## Troubleshooting

**`claude CLI exited with code 1`**
Make sure Claude Code is authenticated. Run `claude` in your terminal and complete the login flow, then restart the pipeline.

**`No requirements.md attachment found`**
Attach a file named exactly `requirements.md` (case-sensitive) to the Jira story.

**Vercel deployment times out**
The pipeline waits up to 5 minutes for a deployment. Check your Vercel project's build logs for errors.

**Jest tests fail after 3 iterations**
The pipeline continues to the GitHub PR stage regardless. Test failures are noted in the Jira comment.
