# DeBruyn Capital — M&A Lead Generation Bot
### Meeting Briefing | V4.2

---

## What does the bot do? (60-second explanation)

> The bot **automatically** searches for mid-market acquisition targets in the DACH region.
> It researches the internet, verifies each company like an M&A analyst,
> filters against our acquisition criteria — and writes fully qualified leads
> directly into Google Sheets. No more manual research.

---

## Overall Architecture

```
 INTERNET / PERPLEXITY AI
 ════════════════════════
         │
         │  "Find real companies with a verified website"
         ▼
 ┌───────────────────────┐
 │   1. DISCOVERY        │   Rotates 7 search strategies:
 │                       │   • Regional (e.g. "Hamburg industrial companies")
 │   → 10 company names  │   • Industry associations
 │     per batch         │   • NorthData
 │                       │   • Commercial register
 └──────────┬────────────┘   • Niche markets
            │
            │  Duplicate check (local, no API call)
            ▼
 ┌───────────────────────┐
 │   2. VERIFICATION     │   Perplexity reads Impressum, LinkedIn,
 │                       │   commercial register, company website
 │   For each new firm   │
 │   → full company      │   Returns: Revenue, employees, CEO,
 │     profile           │   email, phone, ownership type,
 │                       │   founding year, description
 └──────────┬────────────┘
            │
            │  Pre-Flight: plausibility check Rev/Employee
            ▼
 ┌───────────────────────┐
 │   3. FILTER CASCADE   │   7 programmatic gates
 │   (Hard Gates)        │   — no further API call —
 └──────────┬────────────┘
            │
     ┌──────┴───────┐
     ▼              ▼              ▼
 ✅ TARGETS    📋 NEEDS        ❌ REJECTED
 Ready to      Research        (Archive for
 Call                          dedup)
     │
     ▼
 GOOGLE SHEETS
```

---

## The 7 Filter Gates in Detail

All thresholds are configurable (`.env` / `config.json`).

| # | Gate | Condition | Effect |
|---|------|-----------|--------|
| 1 | **Ghost SME** | Parent company revenue > 100M EUR | Rejected |
| 2 | **Subsidiary** | Ownership = subsidiary/group/public | Rejected |
| 3 | **Revenue Min** | Revenue < **4M EUR** | Rejected |
| 4 | **Revenue Max** | Revenue > **15M EUR** | Rejected |
| 5 | **Employees** | < 20 or > 200 employees | Rejected |
| 6 | **AI Fit Verdict** | Perplexity rates "NO FIT" | Rejected |
| 7 | **Soft Gates** | CEO / contact / financials missing | Needs Research |

> **Only companies that pass all 6 Hard Gates + have complete data → Ready to Call.**

---

## 3-Tier Output

```
┌──────────────────────────────────────────────────────────────┐
│  Tab: Targets (Ready to Call)                                │
│  ─────────────────────────────────────────────────────────   │
│  Company | Country | Sector | Website | Description | Why   │
│  interesting | Risks | Revenue | Employees | CEO Name       │
│  CEO Email | CEO Phone | Status | Date | Sources            │
├──────────────────────────────────────────────────────────────┤
│  Tab: Needs Research                                         │
│  Companies that passed the gates but have missing data       │
│  → Manually follow up (NorthData, LinkedIn)                  │
├──────────────────────────────────────────────────────────────┤
│  Tab: Rejected                                               │
│  Archive — prevents re-searching in future sessions          │
└──────────────────────────────────────────────────────────────┘
```

---

## Current Acquisition Criteria

```
Target market:   DACH (DE, AT, CH)
Revenue:         4M — 15M EUR
Employees:       20 — 200
Legal form:      GmbH, KG, AG (owner-operated, family- or professionally managed)
Exclusions:      Subsidiaries, group companies, publicly listed companies
Language:        German search queries, German-language sources preferred
```

---

## Cost & Speed

| Metric | Value |
|--------|-------|
| Cost per qualified lead | **~$0.025** |
| Cost per session (5 leads) | **~$0.12** |
| API | Perplexity Sonar Pro |
| Calls per company | 2 (Discovery + Verify) + optional 1 (Pre-Flight) |
| Batch size | 10 companies per Discovery call |
| Search strategies | 7 rotating archetypes |

---

## Customization (no coding required)

### `.env` — adjust acquisition criteria:
```
REV_MIN=4000000       ← minimum revenue
REV_MAX=15000000      ← maximum revenue
EMP_MIN=20            ← minimum employees
EMP_MAX=200           ← maximum employees
```

### `config.json` — adjust prompts:
```json
{
  "prompts": {
    "buyer_profile_extra": "- Preferred: Northern Germany",
    "discovery_extra":     "- Focus: Trades & Manufacturing",
    "verify_extra":        "- Check for succession situation (owner > 55)"
  }
}
```

---

## Search Strategies (Discovery Archetypes)

The bot automatically rotates through 7 different search patterns so
the same sources are not queried repeatedly:

| # | Strategy | Example |
|---|-----------|---------|
| 1 | Regional | "Hamburg mid-sized companies 50-150 employees" |
| 2 | Industry association | "VDMA member GmbH mechanical engineering Southern Germany" |
| 3 | NorthData | "NorthData GmbH revenue 5-15 million" |
| 4 | Commercial register | "commercial register GmbH founded 1990-2005" |
| 5 | Industrial park | "industrial area Ruhr Valley metal processing" |
| 6 | Niche market | "niche supplier B2B specialized subcontractor" |
| 7 | Succession | "business succession DIHK owner-operated" |

---

## What's next (V4.3)

- [ ] Remove Rev/FTE gate from Hard Gates (too many false rejections)
- [ ] Remove owner-operated requirement from Ownership gate
- [ ] Further refine prompts
- [ ] Add additional search strategies
- [ ] Integrate NorthData / external financial data as backup source

---

*DeBruyn Capital — M&A Agent V4.2 | As of March 2026*
