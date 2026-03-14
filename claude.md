# M&A Agent Rules

## Behavior
- **Plan**: Always update @Task.md before coding.
- **Context**: Use `grep` for search. Only re-read changed files.
- **Atomic**: Minimal line changes only. No full-file rewrites.

## Tech
- Python 3.11, Google Sheets v4, Perplexity Sonar.
- Style: Modular, Class-based, Google Docstrings.
- .venv: Always use the local virtual environment.

## Constraints
- **Internal Only**: No new APIs or subscriptions.
- **Efficiency**: Mandatory Batch-Write for Sheets.