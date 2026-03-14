# M&A Agent - Task Tracking

## Aktueller Status: V5.9.1 Production Ready (2026-03-15)

> V6.0 planning in progress — see `Strategy_Report.md`

### V5.1 — Execution Plan (PLAN ONLY — no code written yet)

---

#### BUG ANALYSIS (from Session_Log.md)

**BUG A — CRITICAL: Unquoted `NOT FOUND` in JSON causes PARSE FAILED → company silently lost**
- Evidence: Line 70: `"revenue_eur":NOT FOUND,"revenue_source":"NOT FOUND"` → full parse failure
- Root cause: LLM sometimes writes JSON value `NOT FOUND` without quotes, producing invalid JSON.
  `json.loads()` throws, the entire verify result is discarded. Good companies are lost (L&R Kältetechnik
  had 110 employees and was family-owned — likely a GOOD FIT).
- Fix: In `extract_json_object()`, add one preprocessing line before `json.loads()`:
  `raw = re.sub(r':\s*NOT FOUND\b', ': "NOT FOUND"', raw)`

**BUG B — HIGH: Markdown ` ```json ``` ` wrapper on discovery "NOT FOUND" causes PARSE FAILED**
- Evidence: Lines 156–162, 176–179: Perplexity wraps its "NOT FOUND" in markdown code blocks.
  `extract_json_array()` cannot parse ` ```json\nNOT FOUND\n``` ` and returns `[]`.
  This wastes API budget and adds empty-candidate batches that accelerate consecutive_failures.
- Fix: In `extract_json_array()` AND `extract_json_object()`, strip markdown fences first:
  `raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.DOTALL)`

**BUG C — HIGH: UK V5.0 run: 5 consecutive "NOT FOUND" → ABORT in 3 seconds ($0.025)**
- Evidence: Lines 204–217 (V5.0 UK, niche "Specialized maintenance services for renewable energy
  equipment"): every discovery batch returns 0 candidates immediately. No smart_retry fires.
  ABORT after 5 failures with 0 companies processed.
- Root cause (two parts):
  1. `smart_retry` logic only fires when candidates were found but all were dupes/useless.
     When discovery returns 0 (after PARSE FAILED or genuine empty response), it increments
     `consecutive_failures` directly — no jump, no retry. With 5 batches each returning 0,
     ABORT is guaranteed in seconds.
  2. The niche "Specialized maintenance services for renewable energy equipment" in English
     is a valid but hyper-specific query. UK renewable energy maintenance is dominated by
     large corporates — legitimate Perplexity "NOT FOUND". The archetype needs to be broader.
- Fix (code): Extend smart_retry to also fire on zero-candidate batches (1 retry max):
  `if not candidates: smart_retry += 1; if smart_retry <= SMART_RETRY_MAX: jump archetype; continue`
- Fix (prompt): UK archetypes should include 2–3 broader fallback archetypes (e.g. no region
  restriction, just UK-wide) to catch cases where geographic sub-region yields nothing.

**BUG D — MEDIUM: Silent fallback to DACH on custom region input**
- Evidence: Not in log yet (no custom region run), but from code inspection line 909.
  `region = region_map.get(region_input, "DACH")` — typing "France" or pressing Enter
  silently routes to DACH. The V5.0 UK session worked correctly (user typed "2"), but
  any future custom input will silently fail.
- Fix: Validation loop (see Step 2 below).

**BUG E — LOW: `PROMPT_BUYER_PROFILE_EXTRA / DISCOVERY_EXTRA / VERIFY_EXTRA` always empty**
- Evidence: All sessions show no extra criteria applied (dead code since V5.0 restructure).
- Root cause: Lines 91–94 read flat keys from `_config["prompts"]` which now has only
  region keys. These three vars are always `""`. Not a crash; just dead.
- Fix: Remove the three `PROMPT_*` module-level vars; they're embedded in templates already.

---

#### STEP 0 — Fix BUGs A, B, C: JSON parsing + smart_retry for zero-candidate batches (`ma_agents.py`)

**0a — Unquoted NOT FOUND fix** (`extract_json_object`):
Add before `json.loads(raw)`:
```python
raw = re.sub(r':\s*NOT FOUND\b', ': "NOT FOUND"', raw)
```

**0b — Markdown fence stripping** (both `extract_json_array` and `extract_json_object`):
Add at the top of each function, before any other processing:
```python
raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.DOTALL)
```

**0c — smart_retry fires on zero-candidate batches too**:
Current logic (roughly): `if not candidates: consecutive_failures += 1; continue`
New logic:
```python
if not candidates:
    if smart_retry < SMART_RETRY_MAX:
        smart_retry += 1
        batch_num += len(_active_prompts["discovery"]["search_archetypes"]) // 2
        write_log(f"Zero-candidate batch — Smart-Retry {smart_retry}/{SMART_RETRY_MAX}")
    else:
        consecutive_failures += 1
        smart_retry = 0
    continue
```

---

#### STEP 1 — Fix BUG E: Remove broken module-level extra-prompt vars (`ma_agents.py`)

Lines 91–94 currently read:
```python
_prompts = _config.get("prompts", {})
PROMPT_BUYER_PROFILE_EXTRA: str = _prompts.get("buyer_profile_extra", "")
PROMPT_DISCOVERY_EXTRA: str = _prompts.get("discovery_extra", "")
PROMPT_VERIFY_EXTRA: str = _prompts.get("verify_extra", "")
```
Replace with: keep `_prompts` line (used nowhere else, harmless), set all three constants to `""`.
These extras are embedded directly in each region's `user_template` in config.json — the flat
module-level overrides are a V3.x remnant that is now dead code.

---

#### STEP 2 — Fix BUG 1 + BUG 3: Robust Region & Count Input Loop (`ma_agents.py`)

Replace the current region block (lines 902–912) with:

```python
# --- REGION SELECTION ---
print("\nSelect Target Region:")
print("  1. DACH      (Germany, Austria, Switzerland)")
print("  2. UK        (United Kingdom)")
print("  3. Benelux   (Netherlands, Belgium, Luxembourg)")
print("  OR type a custom region name (e.g. 'France', 'Nordics')")
region_map = {"1": "DACH", "2": "UK", "3": "Benelux"}
while True:
    region_input = input("\nChoose 1-3 or custom name: ").strip()
    if not region_input:
        print("  Input cannot be empty. Please try again.")
        continue
    if region_input in region_map:
        region = region_map[region_input]
        custom_region_name = region   # e.g. "DACH"
    elif region_input in _config["prompts"]:
        region = region_input         # exact key match (e.g. typed "DACH")
        custom_region_name = region
    else:
        region = "Custom"
        custom_region_name = region_input   # e.g. "France" — injected as {{region}}
    break
global _active_prompts
_active_prompts = _config["prompts"][region]
print(f"  Region: {custom_region_name}")
```

`custom_region_name` is then passed into every `render_template()` call that needs it
(only the Custom profile uses `{{region}}` placeholders — for DACH/UK/Benelux it is a
no-op since their templates don't contain `{{region}}`).

Replace `count_needed` line with:
```python
while True:
    try:
        count_needed = int(input("How many 'Ready to Call' targets? "))
        if count_needed > 0:
            break
        print("  Must be a positive integer.")
    except ValueError:
        print("  Invalid input — please enter a number.")
```

Pass `custom_region_name` into `discover_companies`, `verify_company`, `preflight_check`
as a new `region` keyword argument so `render_template` can substitute `{{region}}`.

---

#### STEP 3 — Add `{{region}}` rendering to the three agent functions (`ma_agents.py`)

**`discover_companies(industry, state, batch_num, region="")`**
- Add `region=region` to the `render_template(user_template, ...)` call.

**`verify_company(data, industry, region="")`**
- Add `region=region` to the `render_template(user_template, ...)` call.

**`preflight_check(data, company_name, industry, region="")`**
- Add `region=region` to the `render_template(_pre["user_template"], ...)` call.

All three callers in `run_ma_agent_loop` pass `region=custom_region_name`.

For DACH/UK/Benelux the `region` kwarg is simply ignored by `render_template`
(no `{{region}}` placeholder in those templates → no side effects).

---

#### STEP 4 — Fix BUG 4: niche_labels print alignment (`ma_agents.py`)

Change:
```python
print(f"  {niche_labels}")
```
To:
```python
print(niche_labels)
```
And update all three `niche_labels` values in `config.json` to include the leading `  ` on every line.

---

#### STEP 5 — Add "Custom" profile to `config.json`

New 4th key under `prompts`: `"Custom"`. Structure identical to DACH/UK/Benelux.

**discovery.search_archetypes** — 10 generic archetypes using `{{region}}`:
```
"{{industry}} SME manufacturer {{region}} owner-managed independent",
"{{industry}} SME {{region}} official business registry independent",
"{{industry}} family business {{region}} mid-market 20-200 employees",
"{{industry}} SME {{region}} trade directory independent supplier",
"{{industry}} {{region}} trade show exhibitor 2023 2024 independent",
"{{industry}} {{region}} industry association member SME",
"{{industry}} business for sale {{region}} succession owner exit",
"{{industry}} subcontractor niche specialist {{region}} owner-managed",
"{{industry}} contract manufacturer {{region}} independent SME",
"{{industry}} OEM technology partner system integrator {{region}} independent"
```

**discovery.niche_suggestions** — same English labels as UK (generic enough).

**discovery.user_template** — same structure as UK but replace hardcoded "UK" / "Ltd/Plc"
with `{{region}}` / "independent limited liability company".

**verify.system** — `"You are an M&A analyst for SMEs. Respond ONLY with the requested JSON..."`

**verify.user_template** — generic: replace `Bundesanzeiger/North Data` with
`"the official national corporate registry for {{region}}"`, `"GmbH/KG/AG"` with
`"independent limited liability company"`, `Impressum` with `"Legal Notice / About Us"`,
`inhabergeführt` ownership types with the UK ones (`owner-managed`, `family-owned`, etc.),
all hardcoded region strings with `{{region}}`.

**preflight.user_template** — replace registry reference with
`"the official national corporate registry for {{region}}"`.

**revenue_source** values in verify JSON spec: add `"registry"` as a valid source
alongside `"estimated"|"NOT FOUND"`.

---

#### STEP 6 — Update version string & log line (`ma_agents.py`)

- Header print: `V5.0` → `V5.1`
- `write_log(...)`: `Pipeline: V5.0` → `Pipeline: V5.1`

---

#### STEP 7 — Update `Task.md` status on completion

Mark all V5.1 items `[x]`, add V5.1 to Versionshistorie.

---

### Verification Checklist (post-execution)
1. `python -c "import ast; ast.parse(open('ma_agents.py').read()); print('OK')"`
2. `python -c "import json; json.load(open('config.json')); print('OK')"`
3. Dry run: enter `"France"` → confirm Custom profile loads, `{{region}}` renders as "France" in log
4. Dry run: press Enter at region prompt → confirm re-prompt loop, no crash
5. Dry run: enter `"abc"` at count prompt → confirm re-prompt loop, no crash
6. Dry run: enter `"1"` (DACH) → confirm DACH archetype 1 appears in log

---

### V5.0 — Erledigt
- [x] Pan-Europe Architektur: DACH / UK / Benelux Region-Selection im CLI
- [x] config.json restrukturiert: `prompts.{DACH,UK,Benelux}.{discovery,verify,preflight}`
- [x] `_active_prompts` global: zur Laufzeit nach Regionsauswahl gesetzt
- [x] `SEARCH_ARCHETYPES` aus Modul-Konstante → dynamisch aus `_active_prompts`
- [x] `discover_companies`, `verify_company`, `preflight_check` auf `_active_prompts` umgestellt
- [x] Smart-Retry Logik ebenfalls auf `_active_prompts` umgestellt
- [x] Region-spezifische Nischen-Vorschläge aus config.json
- [x] Version-String: V4.2 → V5.0

---

## .env Config Referenz
```
REV_MIN=4000000       # 0 = kein Minimum
REV_MAX=15000000      # 0 = kein Maximum
EMP_MIN=20            # 0 = kein Minimum
EMP_MAX=200           # 0 = kein Maximum
REV_PER_EMP_MIN=10000
REV_PER_EMP_MAX=500000
FORBIDDEN_OWNERSHIP=subsidiary,group,listed,public,konzern,tochter
REQUIRED_ROLES=Geschäftsführer,Managing Director,CEO,Inhaber
```

---

## Versionshistorie
- **V2.3:** Fuzzy dedup, error logging, sharper prompts
- **V3.0:** Batch discovery, hard gates, SheetState, 3-tier output (Ready / Needs Research / Abgelehnt)
- **V3.1:** `TargetCriteria` dataclass aus `.env`, domain dedup, Impressum verification, buffer-based batch writing, dynamic gates
- **V3.2:** Micro-batching (4x rounds), 8 Sucharchetypen rotierend, DAX-Ausschluss im Prompt, token-set fuzzy matching, enhanced rejection logging
- **V4.x:** Interne Refactoring-Versionen, Google-style Docstrings, Hygiene
- **V4.2:** Alle 4 kritischen Bugs gefixt (infinite loop, revenue parser, preflight cost, hallucination check)
- **V5.0:** Pan-Europe Architektur — DACH / UK / Benelux region-aware via config.json, CLI region picker, `_active_prompts` runtime dispatch
- **V5.6:** Critical bug fixes — self-blocking domain dedup loop (domains were added before verify), wrong variable `c` in dedup phase, stop-word broadening restored
- **V5.7.1:** Lead maximization — `lookup_dach_ceo()` second-pass Impressum search, "An die Geschäftsführung" CEO fallback, `broaden_industry_gpt()` intelligent niche broadening via GPT-4o-mini, precision Impressum scraping instructions in DACH verify template (intitle:Impressum + intitle:Kontakt OR intitle:Vertretungsberechtigt), README full rewrite
- **V5.8.1:** Archetype 5/6 command-style list-extraction queries (all regions), precision broadening with B2B search angle via GPT-4o-mini
- **V5.9:** Contact-Striker — Deep-Link-Scan subpage scraping (/kontakt/impressum/uber-uns), External Safety-Net search (Maps/Yelp/NorthData), universal CEO "An die Geschäftsführung" fallback requires phone present (all regions), 80% dup-rate Niche Pivot
- **V5.9.1:** Smooth batch display counter (`display_batch` monotonic, separate from `batch_num`), "Skipping Archetype N" log messages on zero-candidate and all-dup smart_retry, `Strategy_Report.md` for V6.0 Hybrid Verification planning
