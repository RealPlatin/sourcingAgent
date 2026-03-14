# M&A Agent - Task Tracking

## Aktueller Status: V4.3 In Arbeit (2026-03-14)

### V4.3 — Offene Punkte
- [ ] README.md erstellen (Prompt-Dokumentation für Nutzer)
- [ ] Alle Prompt-Texte aus ma_agents.py → config.json externalisieren
- [ ] Forbidden-List aus Discovery-API-Prompt entfernen (Token-Ersparnis)
- [ ] SEARCH_ARCHETYPES verbessern: 10 diversere Blickwinkel (Regionen, Verbände, Nachfolge, Nischen)
- [ ] Smart-Retry Logik: bei Duplikat-Batch erst 2x Archetype-Jump, dann consecutive_failures++

### V4.2 — Erledigte Bugs
- [x] **BUG 1 — CRITICAL:** Infinite loop — `consecutive_failures` reset fix via `batch_useful > 0` (Zeile 1087)
- [x] **BUG 2 — HIGH:** Revenue-Parser: `"900.000"` → 900k fix via `re.search(r'\d+\.\d{3}(?!\d)')` (Zeile 720)
- [x] **BUG 3 — MEDIUM:** Preflight-Kosten fix via Boolean-Return + conditional add (Zeile 1047–1049)
- [x] **BUG 4 — MINOR:** Halluzinations-Check fix via `.strip().lower()` (Zeile 1013)

### Nächste Schritte (nach V4.3)
- [ ] Praxistest V4.3 mit echtem Durchlauf

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
