# Scheduler

Personal scheduling assistant prototype.

The project is currently building the pipeline documented in
`names,readme,notes/flow chart.md`:

1. Read events from Google Calendar.
2. Extract tasks/events from informal notes.
3. Classify priority and flexibility.
4. Merge tasks with calendar events.
5. Write the final schedule back to Google Calendar.

## Current milestone: Phase 2 notes extraction

`notes_extractor.py` provides a deterministic baseline for turning one note per
line into structured scheduler records. It does not call an LLM or any network
service, so it is safe to test locally and can later be replaced or augmented by
the prompt in `names,readme,notes/extraction_prompt_template.md`.

Each extracted record contains exactly:

- `title`
- `event_type`: `fixed_time` or `deadline_task`
- `anchor_datetime`: local `YYYY-MM-DDTHH:MM`, or `null`
- `estimated_duration`: minutes, or `null`
- `lock_status`: `locked` or `movable`

## Run the sample extractor

```powershell
python notes_extractor.py "names,readme,notes\Phase 2(sample notes).md" --reference-date 2026-07-16
```

## Run tests

```powershell
python -m unittest discover -s tests -v
```

If the system `python` command is unavailable, use any Python 3.11+ executable.

## Notes

- `API.py` is the current Google Calendar proof of concept.
- `API_kaggle.py` and `API - kaggle.py` are Kaggle-path variants.
- Calendar-write functions should be used carefully because they can create real
  events.
