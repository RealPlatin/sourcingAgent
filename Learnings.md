# M&A Agent - Learnings & Workflow Rules

## Workflow Principles (from CLAUDE.md)
- **Plan first**: Write tasks to Task.md before implementing anything non-trivial
- **Verify before done**: Never mark complete without proving it works (check logs)
- **Self-improvement**: After any correction, add a rule here to prevent recurrence
- **Root causes only**: No workarounds. Find why it broke, fix that.
- **Minimal impact**: Change only what is necessary — don't refactor unrelated code

---

## Bug History & Rules

### Duplicate Loop (2026-03-14)
- **What happened**: Etabo GmbH & Co. KG was found 6x in a row
- **Root cause**: Forbidden list used exact string match; "Etabo GmbH" != "Etabo GmbH & Co. KG"
- **Fix**: Fuzzy matching — `is_duplicate()` checks substring containment in both directions, plus first meaningful name token
- **Rule**: Never use exact string match for company names. German legal suffixes (GmbH, Co. KG, mbH) cause systematic false negatives.

### Silent API Failures (2026-03-14)
- **What happened**: `continue` on API failure gave no indication of what went wrong
- **Fix**: All API calls wrapped in try/except with `write_log(f"ERROR: ...")` + `consecutive_failures` counter
- **Rule**: Always log errors. Add a circuit breaker (`MAX_CONSECUTIVE_FAILURES = 5`) to prevent infinite loops.

### Large Companies Passing Research (2026-03-14)
- **What happened**: Companies with 48M+ EUR revenue kept being returned by Perplexity
- **Fix**: Sharpened system prompt ("NOT 40M, NOT 50M"), added source requirement (Bundesanzeiger/Northdata), and added fallback instruction ("if not 100% sure, pick different company")
- **Rule**: Perplexity sonar-pro tends to suggest well-known companies. Be explicit about what NOT to suggest, and require revenue verification from a named source.

### V3.0 Architecture Decisions (2026-03-14)
- **Decision**: IC-Agent (GPT-4o-mini) replaced by `hard_gate_check()` — code logic, no AI.
- **Why**: GPT validating GPT-generated data is a closed hallucination loop. Programmatic checks on parsed numbers are deterministic and free.
- **Rule**: Never use AI to validate AI output on numerical data. Parse the numbers, check the ranges in code.

- **Decision**: 2-step Perplexity (batch discover → individual verify) instead of 1-step.
- **Why**: Single-call approach gave Perplexity no incentive to cite sources. Dedicated verification call with explicit source requirements produces citable data.
- **Rule**: Separate discovery from verification. Discovery can be fuzzy/batch. Verification must be exact/sourced.

- **Decision**: "Needs Research" tab for companies that pass criteria but lack contact info.
- **Why**: These are real leads, just incomplete. Dropping them wastes the discovery + verification cost.
- **Rule**: Never binary GO/NO-GO. Use a 3-tier system: Ready to Call / Needs Research / Rejected.

### V3.1 Optimization Rules (2026-03-14)
- **Rule**: Buffer all writes, commit once at end. 1 API call per tab, not per company.
- **Rule**: Register SIGINT handler to protect buffered data. Crash = data loss without it.
- **Rule**: Impressum is the single source of truth for CEO/MD name + contact. Quote verbatim, cite URL.
- **Rule**: If a field cannot be verified (CEO, contact, revenue), route to "Needs Research" — never guess or "deduce logically".
- **Rule**: Batch GPT calls across companies, not per-company. Unknown column mapping is the only valid GPT use case.
- **Rule**: Never hardcode filter thresholds. Use `TargetCriteria` dataclass loaded from `.env`. Set to 0 to disable a gate.
- **Rule**: Deduplicate by domain AND company name. Same domain = same company, even if the legal name differs (e.g. holding vs. operating entity).
- **Rule**: Request website URL already in batch discovery — enables domain dedup before spending verification tokens.

### Discovery Failure (2026-03-14)
- **What happened**: Perplexity returned "I cannot fulfill this request" or empty `[]` because the discovery prompt demanded exact revenue/employee ranges that Perplexity couldn't verify.
- **Root cause**: Discovery tried to do Discovery + Verification + Filtering in one prompt. Perplexity refused when it couldn't guarantee the financial constraints.
- **Fix**: "Wide search, tight filter" — Discovery only asks for company NAMES in a niche. No revenue/employee constraints. Verification + hard_gate_check() handle filtering.
- **Rule**: NEVER put financial constraints in the discovery prompt. Discovery = quantity (get 20 names). Verification = quality (check each one). Separation of concerns.
- **Rule**: Always have a fallback prompt. If Perplexity refuses the specific search, try a broader one.

### V3.2 Micro-Batching & Dedup Improvements (2026-03-14)
- **Decision**: 4x micro-batches of 5-7 companies instead of 1x20.
- **Why**: Single batch of 20 leads to repetitive, well-known results. Multiple smaller batches with varied search archetypes produce more diverse candidates.
- **Rule**: Rotate search archetypes (German terms like "Familienunternehmen", "Hidden Champions", "inhabergeführt") across micro-batches for diversity.
- **Rule**: Explicitly exclude DAX companies and large corporations in the system prompt.
- **Rule**: Request website URL in discovery (not just name+city) to enable early domain dedup.

- **Decision**: Token-set fuzzy matching for dedup.
- **Why**: Substring matching misses word-order permutations ("Maschinenbau Schmidt" vs "Schmidt Maschinenbau GmbH"). Token-set overlap catches these.
- **Rule**: Use token-set matching (>=2 meaningful tokens overlap) alongside substring matching.

- **Decision**: OpenAI `response_format={"type": "json_object"}` requires "JSON" in the prompt.
- **Why**: OpenAI API returns 400 error if the messages don't mention JSON when using json_object response format.
- **Rule**: Always include "Output must be in JSON format" in both system and user prompts when using `response_format={"type": "json_object"}`.

- **Decision**: Rejection logs include ownership, revenue, and employee details.
- **Why**: "Ownership forbidden" without context is useless for manual review. Full context enables quick decisions.
- **Rule**: Always log ownership_type, revenue, and employees when rejecting a company.

---

## Rejected Companies Log
- Gesellschaft für Montage und Regeltechnik mbH (GMR) — Umsatz 48,4 Mio. EUR (zu groß)
- Wisag Industrie Service — Umsatz deutlich über 15 Mio. EUR
- Baumüller Reparaturwerk GmbH & Co. KG — Umsatz über 15 Mio. EUR
- Bilfinger SE — Umsatz weit über Zielbereich
