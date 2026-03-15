# V6.0 Strategy: Hybrid Verification Pipeline

## Current Architecture (V5.x — Perplexity-Only)

### Strengths
- Fast: single Perplexity call per company
- Good for public web presence (websites, Impressum, news)

### Weaknesses
- Financials often "NOT FOUND" for companies with low digital footprint
- Private SMEs with no press coverage → revenue/employee data missing
- Leads promoted to "Needs Research" when financials absent

---

## Proposed V6.0: Hybrid Verification

### Phase A — Perplexity (current, unchanged)
Fast list generation + first-pass verify (website, ownership, contact, CEO).
Financials attempted via web sources.

### Phase B — Deep Reasoner (triggered when financials = "NOT FOUND")

**Trigger condition:** `revenue == "NOT FOUND" AND employees == "NOT FOUND"`

**Model options:**
- GPT-4o (via existing OpenAI key) — strong reasoning, structured output
- Gemini 1.5 Pro (new API key required) — large context, native search grounding

**Query strategy per region:**

| Region  | Registry                          | Query pattern |
|---------|-----------------------------------|---------------|
| DACH    | Handelsregister / Bundesanzeiger  | `"{company} Jahresabschluss Handelsregister Umsatz Mitarbeiter"` |
| UK      | Companies House                   | `"{company} Companies House annual return turnover employees"` |
| Benelux | KVK / Crossroads Bank             | `"{company} KVK jaarrekening omzet medewerkers"` |
| Custom  | National registry                 | `"{company} {region} business registry annual report revenue employees"` |

Output: extract `revenue_eur` and `employees_count` from registry filings.
Merge back into `verified` dict before `hard_gate_check`.

---

### Decision Tree

1. `verify_company()` → if revenue AND employees NOT FOUND → Phase B
2. Phase B → parse financials → merge into `verified`
3. `preflight_check` → `hard_gate_check` (unchanged)

**Phase B failure fallback:** If Phase B also returns no financial data (e.g. company has no registry filing at all), the fields remain `"NOT FOUND"` and `hard_gate_check` routes the company to **Needs Research** via the existing soft gate: `"No financial data — needs manual check"`. No data is lost — the company is still written to the sheet with all contact data intact.

---

### Translation Bridge Consideration

The existing `translate_industry(industry, region, custom_region_name)` call translates the user's English niche term into German (DACH) or Dutch (Benelux) before Phase A discovery queries. For Phase B, registry query strings are already templated in the local language (see table above). No additional translation call is needed in Phase B — the company name itself is the primary search key, and all registry APIs accept local-language terms natively.

However, if Phase B expands to **full industry-level registry scans** (searching a registry for all companies in a given sector, rather than looking up a specific company), `translate_industry` must be called before constructing the Phase B query string to ensure correct language.

---

### Escalation Pattern Analogy

V5.9's Contact-Strike pattern provides a proven template for V6.0's Phase B escalation:

| V5.9 Contact-Strike | V6.0 Phase B (proposed) |
|---|---|
| Trigger: email AND phone missing after Phase A verify | Trigger: revenue AND employees NOT FOUND after Phase A verify |
| Pass 1: Deep-Link-Scan (targeted subpage scrape) | Pass 1: GPT-4o registry query (structured financial extraction) |
| Pass 2: External Safety-Net (Maps / Yelp / NorthData) | Pass 2: (optional) Broader web search for annual report PDF |
| Final fallback: route to Needs Research | Final fallback: route to Needs Research with all contact data intact |

The same `api_costs += COST_PERPLEXITY` accounting pattern applies — Phase B calls should increment `api_costs` by the actual GPT-4o token cost.

---

### Prerequisites for V6.0

- Phase B with GPT-4o: no new dependency (OpenAI key already present)
- Phase B with Gemini: new `GOOGLE_API_KEY` in `.env`
- CLAUDE.md constraint "No new APIs" applies to current sessions — V6.0 is a future scope decision

---

### Cost Estimate

| Item | Estimate |
|------|----------|
| GPT-4o call per missing-financial company | ~$0.005–0.015 |
| Expected trigger rate | ~30–40% of verified companies (based on V5.x logs) |
| Net cost per session (50 verified companies) | +$0.50–2.00 |

---

## Status
- V6.0 is **planned, not implemented**
- All code changes require a future session decision on GPT-4o vs. Gemini for Phase B
- No changes to `config.json`, `hard_gate_check()`, or `discover_companies()` are implied
- Logical gaps closed in this document: Phase B failure fallback, translation bridge scope, escalation pattern
