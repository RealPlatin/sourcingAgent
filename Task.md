# M&A Agent - Task Tracking

## V3.1 Final (2026-03-14)

### Implemented
- [x] `TargetCriteria` dataclass loaded from `.env` — zero hardcoded values
- [x] Dynamic gates: set any value to 0 to disable that gate
- [x] Domain-based deduplication (`extract_domain` + `forbidden_domains`)
- [x] Post-verification domain check (catches holding structures)
- [x] Batch discovery requests website URL for early domain dedup
- [x] Dynamic prompts use `CRITERIA.revenue_label()` / `CRITERIA.employee_label()`
- [x] `REQUIRED_ROLES` config for Impressum role matching
- [x] Buffer-based batch writing with SIGINT handler
- [x] Impressum verbatim quoting + citation URLs
- [x] Smart fallbacks: missing CEO/website/contact → Needs Research
- [x] Batch GPT formatting for unknown columns
- [x] Session summary with cost tracking

### Noch offen
- [ ] Praxistest mit echtem Durchlauf

## V3.2 Fixes (2026-03-14)

### Implemented
- [x] Micro-batching: 4 rounds x 5-7 companies statt 1x20
- [x] Search archetypes: 8 verschiedene deutsche Suchbegriffe, rotierend pro Runde
- [x] DAX/Großkonzern-Ausschluss explizit im System-Prompt
- [x] Website-URL bereits in Discovery angefragt (für frühes Domain-Dedup)
- [x] OpenAI JSON fix: System-Prompt + User-Prompt enthalten "Output must be in JSON format"
- [x] Token-set fuzzy matching: Wortgruppen-Überlappung fängt Namenspermutationen
- [x] Enhanced rejection logging: Ownership, Revenue, Employees im Ablehnungsgrund

### Noch offen
- [ ] Praxistest V3.2

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

## Versionshistorie
- V2.3: Fuzzy dedup, error logging, sharper prompts
- V3.0: Batch discovery, hard gates, SheetState, 3-tier output
- V3.1: TargetCriteria dataclass, domain dedup, Impressum verification, batch writing, dynamic gates
