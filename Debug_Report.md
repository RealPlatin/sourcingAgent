# V5.6 Forensic Debug Report
**Generated:** 2026-03-14
**Version audited:** V5.5 (Session_Log.md, ma_agents.py, config.json)

---

## Executive Summary

Two distinct bugs found. **Bug A** is the "Ghost in the Machine" — it explains every false-positive "domain match". **Bug B** is a stale variable that sends the wrong website to every verification call. Together they account for the 14 dupes in the V5.5 UK session and the near-zero useful results pattern across all recent sessions. Perplexity IS returning companies; the pipeline is discarding them incorrectly.

---

## Bug A — CRITICAL: Self-Blocking Domain Loop (False-Positive "Domain Match")

### Location
`run_ma_agent_loop()` — Phase 2 dedup (line 1084) + Phase 3 domain check (lines 1138–1143)

### Root Cause
Phase 2 adds each unique candidate's discovery website to `state.forbidden_domains`:
```python
# Phase 2 — line 1084
state.add_to_forbidden(name, c.get("website"))
```
Phase 3 then checks the *verified* website (from Perplexity verify call) against `state.forbidden_domains`:
```python
# Phase 3 — lines 1138–1143
if v_website:
    domain = extract_domain(v_website)
    if domain and domain in state.forbidden_domains:
        print(f"    -> SKIP (domain dup: {domain})")
        stats["duplicates"] += 1
        continue
```

**Result:** Every candidate that passes Phase 2 adds its own website domain to `forbidden_domains`. When Phase 3 verifies the same company and Perplexity returns the same website, the domain is already in `forbidden_domains` → SKIP. The company blocks itself.

### Evidence
V5.5 UK session "Water quality testing and monitoring services" (log line 706):
- 0 Ready, 0 Research, 0 Rejected
- **14 dupes** — all discovered companies
- Batch 39: 6 candidates → "zero useful results" (all self-blocked in Phase 3)
- Batch 85: 4 candidates → "zero useful results"
- Batch 92: 5 candidates → "zero useful results"

The sheet contained 74 names and 50 domains — all DACH manufacturing companies. Zero overlap with UK water quality companies is possible. Yet 14 dupes. Only explanation: self-blocking.

### Why it doesn't affect ALL companies
If Perplexity's discovery response returns `"website": null` or an empty string, Phase 2 adds nothing to `forbidden_domains` and Phase 3 passes. This is why some companies in earlier sessions (V5.2, V5.3) got through — their discovery JSON lacked a website field.

---

## Bug B — HIGH: Wrong Variable in Verify Call

### Location
`run_ma_agent_loop()`, Phase 3 loop, line 1110

### Root Cause
```python
# Line 1106
for i, candidate in enumerate(unique):
    name = candidate.get("name", "Unknown")
    # BUG: c is leaked from Phase 2's for loop, always = last Phase 2 candidate
    verified = verify_company(name, target_industry, region=custom_region_name, website=c.get("website", ""))
```
`c` is the loop variable from Phase 2's `for c in candidates:`. After Phase 2 ends, `c` retains the **last** candidate from `candidates`, not the current `candidate` being verified in Phase 3.

**Result:** Every company in Phase 3 is verified with the website hint of the *last* candidate from Phase 2. The verify prompt uses `{{website}}` for `site:{{website}} Impressum OR Kontakt` queries — so Perplexity scrapes the wrong company's website for every non-last candidate.

### Impact
- CEO names, emails, phones from wrong company website bleed into the wrong record
- Website-based revenue estimates use the wrong site's headcount
- Some verification failures that appear as hallucinations may be real companies misidentified because Perplexity searched the wrong site

---

## Bug C — MEDIUM: Niche Broadening Produces Dangling Connector

### Location
`run_ma_agent_loop()`, line 1026

### Pattern
"Water quality testing and monitoring services" → broadened to "Water quality testing and" (trailing conjunction). This is not a code bug but a design gap: `words[:-2]` removes the last 2 words but leaves trailing stop-words.

**Evidence:** Log line 726: `NICHE EXHAUSTED: auto-broadened query to 'Water quality testing and'`

---

## Zero Results Analysis

### Confirmed: Perplexity IS returning data — pipeline discards it

Sessions with high zero-candidate rates are caused by:

1. **UK archetypes too restrictive**: Each UK archetype combines niche + geography + ownership type:
   - `"{{industry}} SME Yorkshire Lancashire Midlands owner-managed Ltd"`
   - For "Water quality testing and monitoring services" → Perplexity cannot find companies matching ALL constraints simultaneously → returns `[]`
   - Only archetype index 7 (`"{{industry}} subcontractor specialist niche supplier owner-managed UK"`) works, which is why only batches at archetype 7 (indices mod 10 = 7) returned candidates

2. **Self-blocking (Bug A)** eliminates all returned candidates even when Perplexity cooperates

3. **DACH "Wasser-" artifact**: Log lines 643, 670 (V5.3/V5.4 runs before V5.5 fix): niche "Wasser- und Abwassertechnik" broadened to "Wasser-" before the rstrip fix. V5.5 fixes this to "Wasser" correctly.

### NOT a root cause
- `is_duplicate()` name matching: correct, exact-match only, no partial-string matching
- `extract_domain()`: correct, not matching substrings within domains
- Forbidden domains from the Sheet: no evidence of directory-site contamination in the 50 loaded domains (all are legitimate company websites from DACH sessions)

---

## Google Sheet Analysis

**Spreadsheet ID:** `1MRJHi7KcnmQrYX6RnqtcoN1J6HoT-tg7RtzaZ9M_eRY`

### What `_load_forbidden` loads
Loads from ALL three tabs: Targets, Abgelehnt, Needs Research.
At V5.5 session start: **74 names, 50 domains** loaded.

### Cross-reference with last V5.5 session
The V5.5 UK "Water quality testing" session reports 14 dupes but 0 rejected/research. This means none of those 14 reached `hard_gate_check` — they were all blocked at Phase 3 domain check (Bug A). The sheet's 50 domains are DACH companies and cannot be causing false positives for UK water quality companies. The 14 are all self-blocked.

---

## Fixes to Apply (V5.6)

### Fix 1 — Phase 2: Don't add candidate website to forbidden_domains
Replace the `state.add_to_forbidden(name, c.get("website"))` call with a local batch-dedup set.

### Fix 2 — Phase 3: Correct variable from `c` to `candidate`
Line 1110: `website=c.get("website", "")` → `website=candidate.get("website", "")`

### Fix 3 — UK Archetypes: Add 2 broader fallback archetypes
Replace the 2 least-productive UK archetypes (indices 0 and 1, the geographic-specific ones) with generic fallbacks that work for narrow niches.

### Fix 4 — Broadening: strip trailing stop-words
After `words[:-2]`, strip trailing connectors (and, or, for, in, of, the, a, an, with, by, to).

---

## Recommended V5.6 Archetype Changes (UK)

Replace archetypes 0 and 1 (geographic overloaded) with:
```
"{{industry}} company UK independent SME"
"{{industry}} UK Ltd owner-managed manufacturer services"
```
These are broad enough for Perplexity to return results even for niche searches.
