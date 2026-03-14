# M&A Agent - Learnings & Rules

## Workflow Principles
- **Plan first**: Write tasks to Task.md before implementing anything non-trivial.
- **Verify before done**: Never mark complete without proving it works (check logs).
- **Self-improvement**: After any correction, add a rule here to prevent recurrence.
- **Root causes only**: No workarounds. Find why it broke, fix that.
- **Minimal impact**: Change only what is necessary — don't refactor unrelated code.

---

## Rules by Topic

### Company Names & Deduplication
- Never use exact string match for company names. German legal suffixes (GmbH, Co. KG, mbH) cause systematic false negatives.
- Use token-set matching (≥2 meaningful tokens overlap) alongside substring matching to catch word-order permutations ("Maschinenbau Schmidt" vs. "Schmidt Maschinenbau GmbH").
- Deduplicate by domain AND company name. Same domain = same company, even if the legal name differs (e.g. holding vs. operating entity).
- Request website URL already in batch discovery — enables domain dedup before spending verification tokens.

### Discovery vs. Verification
- NEVER put financial constraints in the discovery prompt. Discovery = quantity (get names in a niche). Verification = quality (check each one). Separation of concerns.
- Always have a fallback prompt. If Perplexity refuses the specific search, try a broader one.
- Rotate search archetypes (German terms: "Familienunternehmen", "Hidden Champions", "inhabergeführt") across micro-batches for diversity.
- Explicitly exclude DAX companies and large corporations in the system prompt.
- Perplexity sonar-pro tends to suggest well-known companies. Be explicit about what NOT to suggest, and require revenue verification from a named source (Bundesanzeiger / Northdata).

### API & Error Handling
- Always log errors. Add a circuit breaker (`MAX_CONSECUTIVE_FAILURES = 5`) to prevent infinite loops.
- Never use AI to validate AI output on numerical data. Parse the numbers, check the ranges in code.
- Separate discovery from verification. Discovery can be fuzzy/batch. Verification must be exact/sourced.
- Always include "Output must be in JSON format" in both system and user prompts when using `response_format={"type": "json_object"}` (OpenAI API requirement).
- Batch GPT calls across companies, not per-company. Unknown column mapping is the only valid GPT use case.

### Data Quality & Output
- Never binary GO/NO-GO. Use a 3-tier system: Ready to Call / Needs Research / Rejected.
- If a field cannot be verified (CEO, contact, revenue), route to "Needs Research" — never guess.
- Impressum is the single source of truth for CEO/MD name + contact. Quote verbatim, cite URL.
- Always log ownership_type, revenue, and employees when rejecting a company (context enables quick manual review).

### Writes & Performance
- Buffer all writes, commit once at end. 1 API call per tab, not per company.
- Register SIGINT handler to protect buffered data. Crash = data loss without it.
- Never hardcode filter thresholds. Use `TargetCriteria` dataclass loaded from `.env`. Set to 0 to disable a gate.

---

## Bug History

### Duplicate Loop
- **Symptom**: Same company found 6x in a row (e.g. Etabo GmbH & Co. KG).
- **Root cause**: Forbidden list used exact string match.
- **Fix**: Fuzzy matching — substring containment in both directions + first meaningful token.

### Silent API Failures
- **Symptom**: `continue` on failure gave no indication of error.
- **Fix**: All API calls wrapped in try/except with `write_log(f"ERROR: ...")` + `consecutive_failures` counter.

### Large Companies Passing
- **Symptom**: Companies with 48M+ EUR revenue returned by Perplexity.
- **Fix**: Sharpened prompt with explicit revenue ceiling and source requirement.

### Revenue Parser (German Thousands)
- **Symptom**: `"900.000"` parsed as 900M instead of 900k (Zschörnig Industriemontage rejected falsely).
- **Root cause**: Single-dot path fell through to plain-number × 1M multiplier.
- **Fix**: Detect `\d+\.\d{3}(?!\d)` pattern before fallback.

### Infinite Loop
- **Symptom**: 448 dupes in one session; loop ran 100+ batches.
- **Root cause**: `consecutive_failures` reset on any non-empty Discovery batch, even if all results were hallucinated.
- **Fix**: `batch_useful_count` flag — only reset on actual progress.

---

## Rejected Companies Log
- Gesellschaft für Montage und Regeltechnik mbH (GMR) — Umsatz 48,4 Mio. EUR (zu groß)
- Wisag Industrie Service — Umsatz deutlich über 15 Mio. EUR
- Baumüller Reparaturwerk GmbH & Co. KG — Umsatz über 15 Mio. EUR
- Bilfinger SE — Umsatz weit über Zielbereich
