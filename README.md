# M&A Command Center V5.9.2

Automated M&A sourcing pipeline that identifies investment-grade SME acquisition targets across Europe. Combines **Perplexity Sonar** (live web research), **GPT-4o-mini** (translation, niche intelligence), and **Google Sheets v4** (structured output).

---

## Quick Start

```bash
cd "DeBruyn Capital"
source .venv/bin/activate
python ma_agents.py
```

You will be prompted for:
1. **Region** — DACH / UK / Benelux / custom name (e.g. "France")
2. **Niche** — choose 1–5 preset or type your own; optionally expand via AI sub-niches
3. **Target count** — how many "Ready to Call" companies to deliver

---

## Pipeline Architecture

```mermaid
flowchart TD
    A([CLI Start]) --> B{Region Select}
    B -->|DACH / UK / Benelux| C[Load Region Profile\nfrom config.json]
    B -->|Custom name| D[Load Custom Profile\ninject {{region}}]
    C --> E{Niche Select}
    D --> E
    E -->|Preset 1-5| F[Target Industry]
    E -->|Custom text| F
    F -->|y| G[GPT-4o-mini\nSub-niche Expansion]
    G --> H[User picks sub-niche]
    H --> F
    F --> I[Translation Bridge\nGPT-4o-mini DACH→DE / Benelux→NL]
    I --> J[Main Loop]

    J --> K[BATCH N\nDiscovery via Perplexity\nRotating Archetype]
    K -->|0 candidates| L{Smart-Retry\n≤ SMART_RETRY_MAX?}
    L -->|yes - jump archetype| K
    L -->|no| M[consecutive_failures++]
    M -->|≥ MAX_CONSECUTIVE_FAILURES| Z([ABORT → Commit])

    K -->|candidates found| N[Dedup\nName + Domain check vs SheetState]
    N -->|all dupes| L
    N -->|>80% dupes| O[Early Niche Pivot\nbroaden_industry_gpt]
    O --> K
    N -->|unique candidates| P[Verify via Perplexity\nFinancials + Ownership + Contact]

    P --> Q[[RAW] Print Rev/Emp/Own]
    Q --> R{Contact\nmissing?}
    R -->|yes| S[[Contact-Strike]\nDeep-Link-Scan]
    S -->|still missing| T[[Contact-Strike]\nExternal Safety-Net\nMaps / NorthData]
    T --> U
    S -->|found| U
    R -->|no| U

    U[Pre-Flight Check\nRev/FTE ratio gate] -->|ratio suspicious| V[[Pre-Flight]\nPerplexity fact-check\nBundesanzeiger / Companies House]
    V --> W
    U -->|ratio OK| W

    W[Hard Gate Check] -->|subsidiary / wrong size / NO FIT| X[Abgelehnt]
    W -->|CEO missing| Y{CEO Fallback}
    Y -->|DACH: Impressum lookup| Y1[[CEO-FIX]\nlookup_dach_ceo]
    Y1 -->|found| W2[Re-run hard gate]
    Y1 -->|not found + phone present| Y2[[CEO-Fallback]\nAn die Geschäftsführung]
    Y2 --> W2
    W2 --> AA[Ready to Call → Targets]
    Y -->|no phone either| AB[Needs Research]
    W -->|no financials| AB
    W -->|no contact| AB
    W -->|all checks pass| AA

    AA --> targets_done{targets_done\n≥ count_needed?}
    targets_done -->|no| J
    targets_done -->|yes| Z2([Commit to Sheets])
```

---

## Region Branching Logic

| Key | Coverage | Registry used in Verify | Legal form expected |
|---|---|---|---|
| DACH | Germany, Austria, Switzerland | Bundesanzeiger, North Data, Handelsregister | GmbH / KG / AG |
| UK | United Kingdom | Companies House | Ltd / Plc |
| Benelux | Netherlands, Belgium, Luxembourg | KVK / KBO (Crossroads Bank) | B.V. / N.V. / SRL |
| Custom | Any region | National registry (generic) | Independent LLC |

**How it works:** On region selection, `_active_prompts = _config["prompts"][region]` sets the runtime prompt profile. Every subsequent Perplexity call — discovery system prompt, user template, verify template, preflight template — is drawn exclusively from this profile. Switching region means switching the entire prompt stack with zero code changes.

**Custom regions** inject a `{{region}}` placeholder (e.g. "France") into generic English templates. DACH / UK / Benelux use hardcoded local terminology (Impressum, inhabergeführt, intitle:Kontakt, etc.) that would be wrong for other countries.

**GEO-FENCE:** Every discovery prompt includes a hard GEO-FENCE instruction that tells Perplexity to immediately discard any company not headquartered in the target region. This operates at the prompt level — not as a post-filter — so off-region results are never returned.

---

## Search Archetypes

Each region profile defines **10 rotating search archetypes** in `config.json`. Each archetype is a different search angle:

| Archetype type | Example (DACH) | Purpose |
|---|---|---|
| Direct niche | `{{industry}} Unternehmen Deutschland` | Broadest sweep |
| Legal form | `{{industry}} GmbH` | Filter to typical SME structure |
| Mittelstand list | `Liste {{industry}} Mittelstand` | Industry directories |
| Specialist | `{{industry}} spezialisierter Anbieter` | Niche players |
| Directory extract | `Extract a list of 10 member companies from...` | Association/registry lists |
| Trade show | `List the 10 most prominent Mittelstand exhibitors...` | Event-verified companies |
| Succession | `{{industry}} Nachfolge Unternehmensverkauf` | Owner-exit signals |
| Contract mfg | `{{industry}} Lohnfertigung Auftragsfertigung` | B2B production angle |
| Supply chain | `{{industry}} Zulieferer Hersteller Anlagenlieferant` | Upstream specialists |
| OEM/integrator | `{{industry}} Technologiepartner OEM Systemintegrator` | Technology partners |

`batch_num % len(archetypes)` rotates through the list. On Smart-Retry, `batch_num` is jumped forward by `len(archetypes) // 2` to force a different archetype on the next call.

---

## Niche Selection & Sub-Niche Expansion

### Flow

1. User sees 5 preset niche suggestions (region-specific, loaded from `config.json`).
2. User picks a preset or types a custom niche.
3. **Optional AI expansion (y/n):** `generate_sub_niches_openai()` calls GPT-4o-mini to produce 5 highly specific sub-niches for the broad term entered.
4. User picks a sub-niche or keeps the original.
5. **Translation Bridge:** `translate_industry()` calls GPT-4o-mini to translate the final niche term:
   - DACH → German (e.g. "CNC Manufacturing" → "CNC-Fertigung")
   - Benelux → Dutch (e.g. "Water Management" → "Waterbeheer")
   - UK / Custom → English (no translation needed)
6. The translated term replaces `{{industry}}` in all discovery archetypes. The original English term is still used in verify/preflight prompts for context.

**Why translate?** German and Dutch SMEs often have no English web presence. A German-language search term yields 3–5× more results from Handelsregister, wer-liefert-was.de, and Mittelstand directories than the equivalent English term.

### Niche Broadening (Automatic)

After **3 consecutive failures** (batches with zero useful results), the pipeline auto-broadens:
- `broaden_industry_gpt()` calls GPT-4o-mini with instruction to find a semantically broader parent category and append a B2B search angle (e.g. "contract manufacturing", "outsourcing partners").
- Output is constrained to stay technical — never broad terms like "Dienstleistung" or "services".
- Falls back to word-stripping (drops last 2 words) if OpenAI is unavailable.
- `translate_industry()` is called again on the broadened term.

### Early Niche Pivot (Automatic)

If **>80% of a discovery batch are known duplicates** (even if some unique candidates remain), the pipeline immediately triggers niche broadening via `broaden_industry_gpt()` without waiting for 3 failures. This prevents wasting API budget on an exhausted niche.

---

## Technical Glossary — Terminal Tags

These tags appear in the terminal output and `Session_Log.md` to identify which pipeline stage processed the company.

### `[RAW]`
```
[RAW] Rev=8500000 | Emp=45 | Own=inhabergeführt | Parent=none
```
Printed immediately after `verify_company()` returns. Shows the **raw financial values extracted from Perplexity** before any parsing, gate checks, or corrections. Useful for spotting hallucinations (e.g. `Rev=8500000000` instead of `8500000`) or missing data (`Rev=NOT FOUND`). The Pre-Flight check fires if the resulting Rev/FTE ratio falls outside 30k–500k EUR.

### `[Contact-Strike]`
```
[Contact-Strike] Deep-link scan...
[Contact-Strike] External safety-net search...
```
Fires when `verify_company()` returns no email **and** no phone. Two escalating rescue passes run automatically:
1. **Deep-Link-Scan** — `deep_link_contact_scan()` sends a targeted Perplexity call instructed to visit `/kontakt`, `/impressum`, `/uber-uns`, `/contact-us`, `/about` and extract `Tel`, `Mail`, `@`, `Zentrale`.
2. **External Safety-Net** — `external_contact_search()` searches Google Maps, Yelp, North Data, and national business registries for the company's public contact record.

A lead only reaches "Needs Research" for missing contact data if **both** passes return nothing.

### `[Pre-Flight]`
```
[PRE-FLIGHT] Rev/FTE=2,800,000€ suspicious — fact-checking...
[PRE-FLIGHT] Corrected: revenue corrected from 280M to 2.8M (data entry error)
```
Fires when the Rev/FTE ratio falls outside the **30k–500k EUR range** — a sign of a data error (e.g. Perplexity confused millions with billions). `preflight_check()` makes an additional Perplexity call to Bundesanzeiger / Companies House / KVK to retrieve the authoritative figure. The corrected values overwrite the raw data before `hard_gate_check` runs. If no correction is found, the original values are kept.

### `[CEO-FIX]`
```
[CEO-FIX] Found via Impressum: Dr. Klaus Müller
```
**DACH-only.** Fires when the initial verify returns no CEO name and the company would otherwise land in "Needs Research". `lookup_dach_ceo()` makes a second targeted Perplexity call that searches `site:<website> intitle:Impressum` for the verbatim `Geschäftsführer:` line. German law requires this on every company website. If found, the gate check is re-run and the company is promoted to "Ready to Call".

### `[CEO-Fallback]`
```
[CEO-Fallback] Set to 'An die Geschäftsführung'
```
**All regions.** Fires when CEO is still missing after `[CEO-FIX]` (or for non-DACH regions), but a **phone number is available**. `ceo_name` is set to the generic salutation `"An die Geschäftsführung"` (German: "To the Management") and the company is promoted to "Ready to Call". The rationale: a direct call can reach the decision-maker even without a named contact. If no phone is available either, the lead stays in "Needs Research".

---

## Hard Gates

All gate thresholds are loaded from `.env` at startup. A threshold of `0` disables that gate.

| Gate | Type | Action |
|---|---|---|
| `parent_revenue_eur > 100M` | Hard reject | → Abgelehnt ("Ghost SME") |
| Subsidiary ownership + named parent | Hard reject | → Abgelehnt |
| `revenue < REV_MIN` or `> REV_MAX` | Hard reject | → Abgelehnt |
| `employees < EMP_MIN` or `> EMP_MAX` | Hard reject | → Abgelehnt |
| `Rev/FTE < REV_PER_EMP_MIN` or `> REV_PER_EMP_MAX` | Hard reject | → Abgelehnt |
| `ownership_type` in FORBIDDEN_OWNERSHIP | Hard reject | → Abgelehnt |
| AI `fit_verdict = NO FIT` | Hard reject | → Abgelehnt |
| CEO name missing | Soft gate | → Needs Research |
| Email AND phone missing | Soft gate | → Needs Research |
| Revenue AND employees both missing | Soft gate | → Needs Research |
| All checks pass | Pass | → Ready to Call |

---

## Output Tabs (Google Sheets)

| Tab | Contents | When a row lands here |
|---|---|---|
| **Targets** | Ready to Call — passed all hard gates | All gates pass, CEO + contact present |
| **Needs Research** | Financially qualified but missing data | Soft gate triggered (CEO, contact, or financials missing) |
| **Abgelehnt** | Rejected — failed hard gates | Wrong size, subsidiary, bad fit verdict |

**Columns in every tab:**
Company Name · Country · Sector/Sub-sector · Website · Short Description · Why Interesting · Risks · Estimated Revenue (EUR) · Employee Count (Est.) · CEO/Founder Name · CEO Email · CEO Phone · Status · Date Added · Notes · Quellen / Links

Data is buffered in memory throughout the session and written in a **single batch API call** at the end (or on Ctrl+C interrupt). This minimises Sheets API quota usage.

---

## `.env` Configuration & Security

```env
# Financial Gates (0 = gate disabled)
REV_MIN=4000000        # Minimum revenue in EUR (4M)
REV_MAX=15000000       # Maximum revenue in EUR (15M)
EMP_MIN=20             # Minimum employee count
EMP_MAX=200            # Maximum employee count
REV_PER_EMP_MIN=10000  # Min revenue per employee (EUR)
REV_PER_EMP_MAX=500000 # Max revenue per employee (EUR)

# Ownership & Contact Filters
FORBIDDEN_OWNERSHIP=subsidiary,group,listed,public,konzern,tochter
REQUIRED_ROLES=Geschäftsführer,Managing Director,CEO,Inhaber

# API Keys — NEVER commit these to git
PERPLEXITY_API_KEY=pplx-...
OPENAI_API_KEY=sk-...
```

### Security Rules

1. **`.env` is in `.gitignore`** — verify this before every `git push`. Never commit API keys.
2. **`token.json` and `credentials.json`** (Google OAuth) are also excluded from git. These grant write access to the target spreadsheet.
3. **`SPREADSHEET_ID`** is stored in `config.json`, which is committed. This is a non-sensitive identifier — the spreadsheet itself is protected by Google OAuth.
4. If an API key is accidentally exposed, rotate it immediately in the Perplexity / OpenAI dashboard and update `.env`.
5. The script reads `.env` via `python-dotenv` at startup. No keys are logged or printed to the terminal.

---

## Smart-Retry & Failure Logic

```
consecutive_failures  — counts batches with zero useful results after Smart-Retry is exhausted
smart_retry           — counts archetype-jump retries within a failure sequence
display_batch         — monotonically increasing display counter (1, 2, 3...)
batch_num             — internal archetype rotation index (can jump forward)
```

| Event | Action |
|---|---|
| Zero candidates from Perplexity | `smart_retry++`; if ≤ SMART_RETRY_MAX: jump archetype by `n//2` |
| All candidates are dupes | `smart_retry++`; same jump logic |
| >80% dupes in batch | Early Niche Pivot (broaden immediately) |
| Batch produces zero useful results after verify | `smart_retry++`; archetype jump |
| `smart_retry >= SMART_RETRY_MAX` | `consecutive_failures++`; reset `smart_retry` |
| `consecutive_failures >= MAX_CONSECUTIVE_FAILURES` (5) | ABORT; commit buffer |
| 3 consecutive failures, niche not yet broadened | Auto-broaden via GPT-4o-mini |

---

## Environment Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Required files (not in git):**
- `.env` — API keys and gate thresholds
- `credentials.json` — Google OAuth client secret (download from Google Cloud Console)
- `token.json` — auto-generated on first run (OAuth refresh token)
