# DeBruyn Capital — M&A Sourcing Agent

Ein automatisierter Pipeline-Agent zur Identifikation von M&A-Akquisitionszielen im deutschen Mittelstand (DACH-Region).

---

## Prozess-Übersicht

```
[Discovery]  →  [Verification]  →  [Pre-Flight Check]  →  [Hard Gates]  →  [Google Sheets]
   Perplexity       Perplexity          Perplexity           Code-Logik       3 Tabs
   10 Namen/Batch   1 Firma/Call        Optional             .env-Werte       Ready / Needs Research / Abgelehnt
```

Pro Batch werden ~10 Firmennamen gefunden, jede Firma einzeln verifiziert, und anschließend durch deterministische Code-Gates gefiltert.

---

## Prompt 1: Discovery

**Zweck:** Findet Firmennamen in einer Branche/Nische. Bewusst ohne Finanzfilter — Menge vor Qualität. Die Filterung passiert erst später.

**Template-Variablen:**

| Variable | Beschreibung | Beispiel |
|----------|-------------|---------|
| `{{archetype}}` | Aktueller Suchwinkel (rotierend aus `search_archetypes`) | `"Finde KMU in Bayern..."` |
| `{{discovery_count}}` | Anzahl gesuchter Firmen pro Batch | `10` |
| `{{discovery_extra}}` | Optionale Ergänzung aus `config.json` → `prompts.discovery_extra` | leer oder `"Fokus: Norddeutschland"` |

**Hebel:** Die `search_archetypes`-Liste in `config.json` steuert, welche Suchwinkel rotiert werden. 10 Archetypes, jeder Batch wählt einen anderen.

---

## Prompt 2: Verification

**Zweck:** Prüft eine einzelne Firma vollständig: Impressum, Finanzdaten, Mitarbeiterzahl, Inhaberschaft, Kontaktdaten. Basis für alle Hard Gates.

**Template-Variablen:**

| Variable | Beschreibung | Quelle |
|----------|-------------|--------|
| `{{company_name}}` | Vollständiger Firmenname | Discovery-Ergebnis |
| `{{industry}}` | Zielnische/Branche | User-Eingabe beim Start |
| `{{rev_label}}` | Umsatz-Zielbereich als Text | Aus `.env` → `REV_MIN`/`REV_MAX` |
| `{{emp_label}}` | Mitarbeiter-Zielbereich als Text | Aus `.env` → `EMP_MIN`/`EMP_MAX` |
| `{{roles_str}}` | Gesuchte Kontaktrollen | Aus `.env` → `REQUIRED_ROLES` |
| `{{buyer_profile_extra}}` | Optionale Ergänzung zum Käuferprofil | `config.json` → `prompts.buyer_profile_extra` |
| `{{verify_extra}}` | Optionale Ergänzung zu den Verifikationsregeln | `config.json` → `prompts.verify_extra` |

**Hebel:** Mit `buyer_profile_extra` kann das Käuferprofil ohne Code-Änderung angepasst werden (z.B. Sektor-Präferenzen, geografische Einschränkungen).

---

## Prompt 3: Pre-Flight Fact-Check

**Zweck:** Feuert **nur wenn** das Umsatz/Mitarbeiter-Verhältnis außerhalb von 30k–500k EUR/FTE liegt — ein Indikator für fehlerhafte Perplexity-Daten. Korrigiert die Zahlen via gezieltem Folge-Call.

**Template-Variablen:**

| Variable | Beschreibung |
|----------|-------------|
| `{{company_name}}` | Vollständiger Firmenname |
| `{{industry}}` | Zielnische/Branche |
| `{{revenue_eur}}` | Gemeldeter Umsatz aus Verification |
| `{{employees_count}}` | Gemeldete Mitarbeiterzahl aus Verification |

**Hebel:** Kann effektiv deaktiviert werden, indem `REV_PER_EMP_MIN=0` und `REV_PER_EMP_MAX=0` in `.env` gesetzt werden.

---

## .env Parameter — Filterlogik (Hard Gates)

Diese Werte steuern die **deterministische Code-Filterung** nach der Verification. `0` = Gate deaktiviert.

| Parameter | Beschreibung | Beispiel |
|-----------|-------------|---------|
| `REV_MIN` | Minimaler Jahresumsatz in EUR | `4000000` (= 4 Mio) |
| `REV_MAX` | Maximaler Jahresumsatz in EUR | `15000000` (= 15 Mio) |
| `EMP_MIN` | Minimale Mitarbeiterzahl | `20` |
| `EMP_MAX` | Maximale Mitarbeiterzahl | `200` |
| `REV_PER_EMP_MIN` | Minimaler Umsatz pro Mitarbeiter | `10000` |
| `REV_PER_EMP_MAX` | Maximaler Umsatz pro Mitarbeiter | `500000` |
| `FORBIDDEN_OWNERSHIP` | Verbotene Eigentumsformen (kommagetrennt) | `subsidiary,group,listed,public,konzern,tochter` |
| `REQUIRED_ROLES` | Erforderliche Kontaktrollen für Verification | `Geschäftsführer,CEO,Inhaber` |
| `PERPLEXITY_API_KEY` | API-Key für Perplexity Sonar Pro | `pplx-...` |

---

## config.json Parameter — Prompt-Anpassung

| Schlüssel | Beschreibung | Direkt editierbar? |
|-----------|-------------|-------------------|
| `SPREADSHEET_ID` | Google Sheets ID (aus URL) | ✅ Ja |
| `prompts.discovery.search_archetypes` | Liste der 10 Suchwinkel für Discovery | ✅ Ja — Haupthebel für Diversität |
| `prompts.discovery.system` | System-Rolle des Discovery-Agents | ✅ Ja |
| `prompts.discovery.user_template` | Discovery-Prompt-Template mit `{{var}}`-Platzhaltern | ✅ Ja (mit Vorsicht) |
| `prompts.verify.system` | System-Rolle des Verification-Agents | ✅ Ja |
| `prompts.verify.user_template` | Verification-Prompt-Template | ✅ Ja (mit Vorsicht) |
| `prompts.preflight.system` | System-Rolle des Fact-Check-Agents | ✅ Ja |
| `prompts.preflight.user_template` | Fact-Check-Prompt-Template | ✅ Ja (mit Vorsicht) |

> **Hinweis zu Template-Syntax:** Platzhalter werden als `{{variablenname}}` geschrieben (doppelte geschweifte Klammern). Einzelne `{...}` im Prompttext (z.B. JSON-Beispiele) bleiben unverändert.

---

## Häufige Anpassungen — "Welchen Hebel ziehe ich?"

| Ziel | Hebel | Datei |
|------|-------|-------|
| Andere Branche suchen | Industry-Eingabe beim Start des Scripts | Interaktiv |
| Größere Unternehmen | `REV_MIN`, `REV_MAX`, `EMP_MIN`, `EMP_MAX` erhöhen | `.env` |
| Mehr regionale Diversität | Neue Einträge in `search_archetypes` hinzufügen | `config.json` |
| Eigenes Käuferprofil ergänzen | `prompts.buyer_profile_extra` befüllen | `config.json` |
| Strengere Eigentumsfilter | `FORBIDDEN_OWNERSHIP` ergänzen | `.env` |
| Verification-Fokus ändern | `prompts.verify_extra` befüllen | `config.json` |
| Discovery-Suche schärfen | `prompts.discovery_extra` befüllen | `config.json` |
| Pre-Flight deaktivieren | `REV_PER_EMP_MIN=0` und `REV_PER_EMP_MAX=0` | `.env` |
| Mehr Firmen pro Batch | `DISCOVERY_COUNT` in `ma_agents.py` erhöhen | `ma_agents.py` (Zeile ~26) |

---

## Start

```bash
cd "DeBruyn Capital"
source .venv/bin/activate
python ma_agents.py
```

## Umgebung wiederherstellen

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Ausgabe-Tabs in Google Sheets

| Tab | Inhalt |
|-----|--------|
| **Ready to Call** | Firma verifiziert, alle Gates bestanden, Kontaktdaten vorhanden |
| **Needs Research** | Firma interessant, aber Kontaktdaten oder Finanzdaten fehlen |
| **Abgelehnt** | Firma durch Hard Gates ausgeschlossen (mit Ablehnungsgrund) |
