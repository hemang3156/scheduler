# Progress Report

Date: July 17, 2026

## Current Progress

The notes-to-structured-data prototype now has a working notebook flow in
`notes_to_data.ipynb` that reads fresh sample notes from
`data/import/sample_notes.md`, builds the extraction prompt, sends the request
through the Gemini client, and saves the parsed output to
`data/export/response.json`.

This moves the project from a prompt-only extraction design toward a runnable
LLM-backed proof of concept for Phase 2 of the scheduler pipeline.

## What Was Added

- New raw input notes in `data/import/sample_notes.md`
- Notebook-based extraction flow in `notes_to_data.ipynb`
- Saved model response in `data/export/response.json`

## What The Notebook Is Doing

The notebook currently:

1. Reads the current local date for relative date resolution.
2. Loads raw notes from `data/import/sample_notes.md`.
3. Loads the extraction prompt template from
   `data/import/extraction_prompt_template.md`.
4. Initializes the Gemini client from `gemini_api_key.txt`.
5. Sends the filled prompt to the `gemini-3.5-flash` model.
6. Parses the JSON response and writes it to `data/export/response.json`.

## Output Summary

The latest run produced 7 structured scheduler items from 7 informal notes.

Highlights from the generated response:

- Exact dated meeting extracted correctly:
  `ACE promotional layout meet` -> `2026-08-16T18:00`
- Self-work item kept movable even with a date and time:
  `ML pipeline debug - YOLO` -> `2026-08-17T20:00`
- Relative date handling worked for:
  `this sunday`, `this weekend`, `today`, and `tomorrow`
- Explicit duration was preserved for:
  `Flute practice` -> `estimated_duration: 30`
- Near-term deadline was marked locked:
  `Finish CCUS report` -> `2026-07-18T23:59`

## Status Assessment

The end-to-end extraction path is working for a realistic mixed-note sample.
The notebook can now be used as a quick validation harness while the extraction
rules and prompt behavior are refined.

## Open Observation

One item is still a good candidate for review:

- `Ask papa about schedule` was classified as `fixed_time` with
  `2026-07-17T00:00`.

This is acceptable under the current prompt rule for uncertain same-day timing,
but it may be worth deciding whether conversational reminders like this should
stay `fixed_time` or be treated more like flexible tasks.

## Next Steps

- Compare notebook output against expected extraction examples for consistency.
- Add a few more ambiguous note patterns to stress-test prompt behavior.
- Decide how to classify call/reminder-style items with no real time attached.
- Use this notebook output as the handoff input for the next scheduling stage.
