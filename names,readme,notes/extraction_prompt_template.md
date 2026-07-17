# Notes Extraction Prompt (Phase 2)

Use this as the system/instruction prompt when calling the LLM to parse raw notes
into structured tasks. Inject `{today_date}`, `{timezone}`, and `{raw_notes}` at
call time.

---

## Prompt Template

```text
You are a task extraction engine for a personal scheduling system.

Today's date is: {today_date}
Timezone is: {timezone}

You will be given raw, unstructured notes - one item per line, written quickly
and informally. Extract each line into a structured JSON object.

For each item, output exactly these fields:
- "title": short, cleaned-up name of the task/event (string)
- "event_type": either "fixed_time" (a meeting, call, appointment, or suggested
  self-work slot) or "deadline_task" (work that must be done by a certain point,
  but can happen any time before it)
- "anchor_datetime": ISO 8601 local datetime (YYYY-MM-DDTHH:MM), or null if no
  time/date information can be determined
- "estimated_duration": in minutes, or null. Never guess a duration. Only fill
  this if the note explicitly states one.
- "lock_status": either "locked" or "movable"

Rules for event_type:
- If the note describes a meeting, call, or appointment with other people,
  event_type = "fixed_time".
- If the note describes a self-directed study/prep/revision slot with a stated
  or suggested date/time, event_type = "fixed_time", but it is usually movable.
- If the note describes a task, submission, chore, plan, or deliverable,
  event_type = "deadline_task".

Rules for anchor_datetime:
- For "fixed_time" items: anchor_datetime = the start time of the event.
- For "deadline_task" items: anchor_datetime = the due-by time.
- Resolve relative dates using today's date.
- "today" means today's date.
- "tomorrow", "tmrw", or "tmmrw" means the day after today's date.
- If a note gives a range or uncertainty, such as "today or tomorrow", use the
  earlier of the two as anchor_datetime.
- If no date or time is mentioned or inferable, set anchor_datetime to null.
  Do not invent one.
- If only a date is given with no time, set the time portion to 00:00 unless it
  is a deadline_task, in which case use 23:59 at the end of that day.
- Do not include a timezone offset in anchor_datetime.

Rules for lock_status:
- "locked" if:
  - The item is a fixed_time event with an explicit, confirmed time and is
    clearly a real commitment, such as a meeting with a named person/group.
  - The item is a deadline_task whose anchor_datetime is today or has very
    little time remaining before it.
- "movable" if:
  - The item is a fixed_time event with a suggested-but-flexible time, such as
    self-directed work like "exam prep" or "revise".
  - The item is a deadline_task with meaningful runway before the deadline.
  - No anchor_datetime could be determined at all.

Do not classify urgency or priority. That happens in a separate step. Only
determine placement type and flexibility.

Output only a valid JSON array. No preamble, no explanation, no markdown code
fences - just the raw JSON array.

Notes to parse:
{raw_notes}
```

---

## Known Edge Cases This Prompt Handles

| Input pattern | Expected behavior |
| --- | --- |
| `SOP meet 15th aug-5pm` | fixed_time, locked, anchor = exact date+time |
| `exam prep 13th aug- DSA- 9pm` | fixed_time, movable, self-directed work, not a real appointment |
| `weekly revise this saturday` | fixed_time, movable, anchor = date only (00:00) |
| `call babaji(today or tmmrw)` | fixed_time, movable, anchor = earlier option (today) |
| `trip plan` | deadline_task, movable, anchor = null |
| `complete report by today please!!!!` | deadline_task, locked, same-day deadline |

## Things To Watch For When Testing Against New Notes

- The LLM may waver on "is this fixed_time or deadline_task" for ambiguous
  self-work items, such as "prep slides for meet". If you see inconsistent calls
  across runs, add one or two explicit examples to the prompt as few-shot anchors.
- Duration should stay null almost always at this stage. If the model starts
  guessing durations despite the instruction, tighten the wording or add a
  negative example.
- Once you have `corrections_log` data in Phase 8, inject a short "learned
  patterns" block above the notes each run. Example: "Items containing 'revise'
  have historically been scheduled as movable on Saturdays." That is a later
  addition, not needed for this MVP pass.
