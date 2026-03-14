# M&A Agent - Core Rules

## Behavior
- **Plan First**: Always use Planning Mode before coding. Write to @Task.md.
- **Minimal Context**: Do not re-read files unless changed. Use `grep` for searches.
- **Atomic Changes**: Change only necessary lines, no full-file rewrites.

## Tech Stack
- Python 3.10+, Google Sheets API v4, Perplexity Sonar.
- Coding Style: Modular, Class-based, Google-style Docstrings.

## Critical Constraints
- No new external APIs/Subscriptions (Internal logic only).
- Batch-Write to Sheets is mandatory for new features.