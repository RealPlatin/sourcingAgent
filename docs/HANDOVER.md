# M&A Command Center — Intern Handover Guide

This guide covers everything you need to set up the tool, run a session, and handle common situations. Read the full `README.md` for architecture details.

---

## 1. Prerequisites

- **Python 3.11** installed (check: `python3.11 --version`)
- **Git** access to this repository
- Access to the following (request from your supervisor):
  - Perplexity API key (`pplx-...`)
  - OpenAI API key (`sk-...`)
  - Google Cloud credentials JSON for the M&A spreadsheet
  - The target Google Spreadsheet ID

---

## 2. One-Time Setup

### 2a. Create the virtual environment

```bash
cd "DeBruyn Capital"
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2b. Create the `.env` file

Create a file named `.env` in the project root. **This file must never be committed to git.**

```env
PERPLEXITY_API_KEY=pplx-YOUR_KEY_HERE
OPENAI_API_KEY=sk-YOUR_KEY_HERE

REV_MIN=4000000
REV_MAX=15000000
EMP_MIN=20
EMP_MAX=200
REV_PER_EMP_MIN=10000
REV_PER_EMP_MAX=500000

FORBIDDEN_OWNERSHIP=subsidiary,group,listed,public,konzern,tochter
REQUIRED_ROLES=Geschäftsführer,Managing Director,CEO,Inhaber
```

Adjust the revenue and employee thresholds if your mandate changes. Setting a value to `0` disables that gate.

### 2c. Place Google credentials

Copy `credentials.json` (Google OAuth client secret) into the project root. This file grants the script access to write to the M&A spreadsheet.

On **first run**, a browser window opens for Google OAuth. Approve access — this creates `token.json` which persists the session. You will not need to re-authenticate unless `token.json` is deleted.

### 2d. Verify the Spreadsheet ID

Open `config.json` and confirm `SPREADSHEET_ID` matches the live M&A spreadsheet. If it does not, update it or enter the correct ID when prompted at startup.

---

## 3. Activating the Environment (Every Session)

```bash
cd "DeBruyn Capital"
source .venv/bin/activate
python src/ma_agents.py
```

You must run `source .venv/bin/activate` every time you open a new terminal. The prompt will show `(.venv)` when active.

---

## 4. Daily Operations

### 4a. Choosing a region

At startup you will see:
```
Select Target Region:
  1. DACH    (Germany, Austria, Switzerland)
  2. UK      (United Kingdom)
  3. Benelux (Netherlands, Belgium, Luxembourg)
  OR type a custom region name (e.g. 'France', 'Nordics')
```

- For standard mandates use `1`, `2`, or `3`.
- For a custom region (e.g. France, Nordics, CEE), type the region name exactly. The pipeline uses generic English templates with `{{region}}` substitution.

### 4b. Choosing a niche

You will see 5 preset niche options relevant to the region. You can:
- Enter `1`–`5` to select a preset
- Type your own niche (e.g. "fire protection systems", "industrial cooling")

**AI sub-niche expansion (recommended for new mandates):**
If OpenAI is configured, you will be asked `Want AI sub-niche expansion? (y/n)`. Entering `y` generates 5 highly specific sub-niches via GPT-4o-mini. This is useful when the mandate is broad — it finds more targeted companies.

**How to pick a good niche:**
- Be specific enough to get relevant companies, but not so narrow that fewer than 50 exist in the region.
- Good: "Industrial vacuum systems", "Cleanroom construction", "Hydraulic component repair"
- Too broad: "Manufacturing", "Engineering services"
- Too narrow: "Vacuum systems for semiconductor fabs in Bavaria"

### 4c. Setting the target count

Enter how many "Ready to Call" targets you need. Start with `10` for a first run. The pipeline stops as soon as this count is reached and commits to Sheets.

### 4d. Reading terminal output

| Output | Meaning |
|---|---|
| `BATCH 3 \| Need 7 more` | Currently on batch 3, need 7 more "Ready to Call" |
| `Found 8 real candidates` | Discovery returned 8 non-hallucinated companies |
| `SKIP (dup: exact match)` | Company already in the sheet — skipped |
| `[RAW] Rev=8500000 \| Emp=45` | Raw data from Perplexity verify call |
| `[Contact-Strike] Deep-link scan...` | Phone/email missing — escalating |
| `[PRE-FLIGHT] Rev/FTE suspicious` | Revenue/employee ratio looks wrong — fact-checking |
| `[CEO-FIX] Found via Impressum: ...` | CEO found on second pass (DACH only) |
| `[CEO-Fallback] Set to 'To Management'` | Generic fallback CEO name applied |
| `-> READY TO CALL ✓` | Company passes all gates → written to Targets tab |
| `-> NEEDS RESEARCH: No financial data` | Company is real but data is incomplete |
| `-> REJECTED: Revenue 320000 EUR < min` | Company failed a hard gate |

---

## 5. After the Session

When the pipeline finishes (or you press Ctrl+C), it commits all buffered data to Google Sheets in one batch write. You will see:

```
COMMITTING TO GOOGLE SHEETS...
  Targets: 10 rows written
  Needs Research: 3 rows written
  Denied: 12 rows written
```

Open the spreadsheet and review:
- **Targets tab** — companies ready to be called. Sort by confidence or revenue.
- **Needs Research tab** — companies that passed financial gates but lack CEO/contact/financials. These need a manual web search (15–30 min each) before calling.

---

## 6. Handling "Needs Research" Leads

A lead in "Needs Research" means the AI found a promising company but could not complete the profile. Common missing fields:

| Missing field | How to find it manually |
|---|---|
| `CEO name` | Check the Impressum page of the website, LinkedIn, or Bundesanzeiger |
| `Phone` | Company website footer, Google Maps listing |
| `Email` | Impressum page, contact form domain (info@...) |
| `Revenue` | Bundesanzeiger (DACH), Companies House (UK), KVK (Netherlands) |

Once you have the missing data, update the row directly in Sheets and move it to the Targets tab manually.

---

## 7. Troubleshooting

### Perplexity returns no results (zero candidates every batch)

**Symptoms:** Terminal shows `Discovery returned nothing` repeatedly, then `ABORT after 5 failures`.

**Causes and fixes:**

| Cause | Fix |
|---|---|
| Niche is too hyper-specific for the region | Re-run with a broader niche |
| Region is too narrow (e.g. a specific city) | Use the country name instead |
| Perplexity rate limit (429 error in log) | Wait 60 seconds and re-run |
| Perplexity API key expired | Check Perplexity dashboard, update `.env` |
| Network timeout | Check internet connection; the script retries automatically |

### Perplexity returns the same companies every batch

**Symptom:** `All candidates were duplicates` on multiple consecutive batches.

The pipeline handles this automatically via Smart-Retry (archetype jumping) and Early Niche Pivot. If it persists across a full session, the niche is exhausted in this region. Try:
- A different archetype angle (e.g. "contract manufacturing" instead of "industrial services")
- A broader parent niche
- A different sub-region (e.g. "Northern Germany" instead of just "DACH")

### OpenAI not available (translation / sub-niche skipped)

**Symptom:** `[Warning] Translation failed` or no sub-niche expansion prompt appears.

The pipeline falls back gracefully:
- Translation: uses the original English term
- Sub-niche expansion: prompt is skipped entirely
- Niche broadening: falls back to word-stripping (removes last 2 words)

Fix: check `OPENAI_API_KEY` in `.env` and verify the key is active in the OpenAI dashboard.

### Google Sheets auth error

**Symptom:** `Error 403` or `invalid_grant` at startup.

Fix: delete `token.json` and re-run. A browser window will open for re-authentication.

### `config.json` not found or `SPREADSHEET_ID` missing

**Symptom:** Script prompts `Enter Spreadsheet ID:` at startup.

Fix: paste the spreadsheet ID from the URL of the Google Sheet (`https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`).

### Session interrupted mid-run (Ctrl+C)

The interrupt handler commits all buffered data to Sheets before exiting. You will see `Interrupt received — committing buffered data...`. No data is lost. Simply re-run to continue sourcing.

---

## 8. Key Files Reference

| File | Purpose |
|---|---|
| `ma_agents.py` | Main pipeline script |
| `config.json` | Region profiles, archetypes, prompt templates, Spreadsheet ID |
| `.env` | API keys and gate thresholds (never commit) |
| `credentials.json` | Google OAuth client secret (never commit) |
| `token.json` | Google OAuth refresh token (auto-generated, never commit) |
| `Session_Log.md` | Timestamped log of every API call, decision, and error |
| `README.md` | Full technical documentation |
| `Strategy_Report.md` | V6.0 roadmap (Hybrid Verification — not yet implemented) |
| `requirements.txt` | Python dependencies |

---

## 9. Quick Verification Checklist

Before your first real run, confirm:

```bash
# Python syntax check
.venv/bin/python -c "import ast; ast.parse(open('ma_agents.py').read()); print('Syntax OK')"

# Config JSON valid
.venv/bin/python -c "import json; json.load(open('config.json')); print('Config OK')"

# .env loaded
.venv/bin/python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('Perplexity key present:', bool(os.getenv('PERPLEXITY_API_KEY')))"
```

All three should print OK / `True` before running a live session.
