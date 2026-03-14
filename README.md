# M&A Command Center V5.9

Automated M&A pipeline for identifying investment-grade SME acquisition targets across Europe. Combines Perplexity Sonar (web research) with GPT-4o-mini (translation, niche broadening, sub-niche expansion) and Google Sheets (output).

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

## Multi-Region Support

| Key     | Coverage                              |
|---------|---------------------------------------|
| DACH    | Germany, Austria, Switzerland         |
| UK      | United Kingdom                        |
| Benelux | Netherlands, Belgium, Luxembourg      |
| Custom  | Any region — type it at the prompt    |

Region selection loads a dedicated prompt profile (`config.json → prompts.<region>`), including 10 search archetypes, niche suggestions, verify template, and preflight template.

**Custom regions** inject `{{region}}` into generic English templates. All other regions use hardcoded local terminology (e.g. DACH uses Bundesanzeiger, Handelsregister, inhabergeführt).

---

## Translation Bridge

For DACH and Benelux sessions, the English niche term entered by the user is automatically translated to German / Dutch via GPT-4o-mini before Perplexity search. This prevents zero-result batches on non-English niches.

Falls back to the original term if OpenAI is unavailable.

---

## Geo-Fencing

Every discovery prompt includes a hard GEO-FENCE instruction. Companies not headquartered in the selected region are discarded at the prompt level. For DACH, the system uses local registry terminology to surface SMEs with limited English web presence.

---

## Precision Impressum Scraping

The DACH verify step uses two targeted Perplexity queries per company:

1. `site:<website> intitle:Impressum`
2. `site:<website> intitle:Kontakt OR intitle:Vertretungsberechtigt`

The agent extracts the verbatim name after `Geschäftsführer:`, `Vertreten durch:`, or `Inhaber:`. German law requires every company website to publish this — if not found on the first pass, a second-pass `lookup_dach_ceo()` call is made.

---

## Contact-Striker (V5.9)

When the initial verify pass returns no phone or email, two escalating rescue passes fire automatically:

1. **Deep-Link-Scan** — a targeted Perplexity call explicitly visits `/kontakt`, `/impressum`, `/uber-uns`, `/contact-us`, `/about` and searches for `Tel`, `Telefon`, `Mail`, `@`, `Ansprechpartner`, `Zentrale`.
2. **External Safety-Net** — if the Deep-Link-Scan still finds nothing, a second Perplexity call searches Google Maps, Yelp, North Data, and national business registries for the company's official contact details.

A lead only lands in "Needs Research" for missing contact data if **both** passes return nothing.

---

## CEO Fallback Logic

If a financially qualified company would otherwise land in "Needs Research" due to a missing CEO name:

1. **DACH only**: A second Perplexity call (`lookup_dach_ceo`) searches the Impressum directly for `Geschäftsführer`.
2. **All regions (phone required)**: If a phone number is available but CEO is still missing, `ceo_name` is set to `"An die Geschäftsführung"` (generic salutation) and the company is promoted to **Ready to Call**. If no phone was found, the lead stays in "Needs Research".

This ensures no financially qualified lead with a reachable phone number is lost to a missing contact name.

---

## Intelligent Niche Broadening

After 3 consecutive failures (empty or all-duplicate batches), the pipeline automatically broadens the search niche:

- **GPT-4o-mini** identifies a semantically broader parent category with a B2B search angle appended (e.g. "contract manufacturing", "industrial testing", "outsourcing partners"), in the correct language (German for DACH, Dutch for Benelux, English otherwise).
- Example: "Specialized maintenance services for renewable energy equipment" → "Renewable Energy Services industrial testing"
- Output stays technical and within the production/technology sector (never broad terms like "Dienstleistung" or "services").
- Falls back to word-stripping (last 2 words removed) if OpenAI is unavailable.

---

## Pipeline Architecture (5 Phases)

```
DISCOVERY  →  DEDUP  →  VERIFY  →  CEO FALLBACK  →  HARD GATES  →  SHEETS
```

| Phase         | Tool        | Description                                         |
|---------------|-------------|-----------------------------------------------------|
| Discovery     | Perplexity  | 10 rotating archetypes, geo-fenced                 |
| Dedup         | SheetState  | Name fuzzy match + domain dedup                    |
| Verify        | Perplexity  | Full financial + ownership + contact deep-dive     |
| CEO Fallback  | Perplexity  | Second-pass Impressum search (DACH) + generic name |
| Hard Gates    | Python      | Rev/FTE triangulation, ownership, role check       |
| Sheets        | Sheets v4   | Batch-write to Targets / Needs Research / Abgelehnt|

**Smart-Retry**: on zero-candidate or all-duplicate batches, jumps to a different archetype (up to `SMART_RETRY_MAX=2` times) before counting as a failure.

**Early Niche Pivot**: if >80% of a discovery batch are known duplicates (even if some unique candidates remain), the pipeline immediately broadens the niche via `broaden_industry_gpt()` rather than wasting further API calls on an exhausted niche.

---

## `.env` Configuration

```env
REV_MIN=4000000        # 0 = no minimum
REV_MAX=15000000       # 0 = no maximum
EMP_MIN=20             # 0 = no minimum
EMP_MAX=200            # 0 = no maximum
REV_PER_EMP_MIN=10000
REV_PER_EMP_MAX=500000
FORBIDDEN_OWNERSHIP=subsidiary,group,listed,public,konzern,tochter
REQUIRED_ROLES=Geschäftsführer,Managing Director,CEO,Inhaber
PERPLEXITY_API_KEY=pplx-...
OPENAI_API_KEY=sk-...
```

---

## Output Tabs (Google Sheets)

| Tab              | Contents                                                  |
|------------------|-----------------------------------------------------------|
| Targets          | Ready to Call — passed all hard gates                    |
| Needs Research   | Financially qualified but missing data (e.g. revenue)    |
| Abgelehnt        | Rejected — failed hard gates (wrong size, subsidiary...) |

Each row includes: company name, website, city, revenue, employees, ownership type, CEO/contact, email, phone, Impressum URL, LinkedIn, fit verdict, confidence, notes.

---

## Environment Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
