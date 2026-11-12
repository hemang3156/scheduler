"""
Prompt templates for unstructured notes extraction, task prioritization, and preference curve evolution.
"""

NOTES_EXTRACTION_PROMPT_TEMPLATE = """You are an expert executive task extraction and scoring engine for an autonomous personal calendar scheduler.

Reference Anchors:
- Current Reference Date: {current_date}
- Current Time: {current_time}
- Current Timezone: {timezone}

Input Notes:
\"\"\"
{raw_notes}
\"\"\"

Instructions:
1. Parse every informal note or task item from the text above.
2. For each task, determine:
   - "title": Clean, concise title.
   - "event_type": One of "lecture", "meeting", "routine", "evaluative", "others".
   - "start": An explicit ISO local datetime (YYYY-MM-DDTHH:MM:SS) if specifically scheduled, otherwise null. If a quiz, exam, presentation, or similar super important milestone is in the task list with only a date and no specific time, set "start" to that date at 00:00:00 (YYYY-MM-DDT00:00:00).
   - "end": An explicit ISO local deadline or end datetime (YYYY-MM-DDTHH:MM:SS) ONLY if an explicit deadline or end time was stated with keywords like 'by', 'before', 'until', 'to'. If only a start or preferred time is mentioned (e.g. '10am'), set "end" to null. If a quiz, exam, presentation, or similar super important milestone is in the task list with only a date and no specific time, set "end" to that date at 23:59:59 (YYYY-MM-DDT23:59:59). NEVER set an 'end' earlier than Current Time ({current_time}).
   - "estimated_duration": Realistic duration in minutes (positive integer, minimum 15 minutes unless trivial). Default self-study sessions to 45-60 minutes unless stated otherwise. Quick checks/reminders to 15-30 minutes.
   - "importance": Integer from 1 (trivial) to 5 (critical deliverable/health/high-stakes).
   - "urgency": Integer from 1 (flexible/someday) to 5 (due today/ASAP).
   - "is_movable": False for fixed meetings or scheduled classes with others; True for solo work, practice, chores. If an important quiz, exam, or presentation has only a date and no fixed start hour, mark is_movable=True so it can be scheduled within the day.
   - "priority_category": "urgent", "important", "routine", or "low_priority".
   - "scoring_rationale": One concise sentence explaining the scores.
3. Priority Processing & Time Windows:
   - Preparation vs Milestones:
     - Distinguish between future milestones (e.g. 'quiz-25th', 'krd quiz -20th', 'lab skill test - 19th') and preparation tasks (e.g. 'pdp quiz prep', 'krd quiz prep', 'study for exam').
     - Unless a prep task explicitly specifies a future date in its text, preparation tasks are for TODAY ({current_date}).
   - Scheduling Window for Today's Tasks:
     - Today's tasks (self-study, prep, project work, routine items) are to be scheduled between Current Time ({current_time}) and the end of today's operating window (1:00 AM tonight, {next_date}T01:00:00).
     - Unless an explicit 'by/before' deadline is written by the user, set 'end' to null so they can be placed flexibly throughout the evening up until 1:00 AM.
   - Strict Negative Constraint Against Past Deadlines:
     - NEVER set an 'end' (deadline) earlier than Current Time ({current_time}). Deadlines before Current Time are expired and invalid.
     - Do NOT invent or hallucinate arbitrary afternoon or early evening deadlines (e.g., 15:00, 17:00, 19:00) that artificially constrain flexible tasks.
   - High-Priority Task Protection:
     - Critical academic deliverables, vivas, exams, graded evaluations, and high-stakes meetings with professors or evaluators must be scored with high importance (4-5) and urgency (4-5), and priority_category="urgent".
     - If a quiz, exam, presentation, viva, or similar super important task/milestone is present in the task list with only a date and no specific time, put the event's start and end times as 00:00:00 and 23:59:59 on that date (start: "YYYY-MM-DDT00:00:00", end: "YYYY-MM-DDT23:59:59") and mark is_movable=True.
     - General project assistance, casual study, or non-urgent queries should be scored lower (importance 2-3, urgency 2-3).
     - High priority tasks must be processed first so lower priority or routine tasks do not take up their space or block them.
   - Time Windows vs Fixed Appointments:
     - If a note specifies a time window or range (e.g. "between 2pm-6pm" or "between 2-6pm"), do NOT pin it to start at the window start (14:00) with is_movable=False.
     - Mark is_movable=True, set "end" to the window deadline (e.g. 18:00), and leave "start" as null (or preferred anchor). This allows multiple tasks within that window to place flexibly without falsely colliding at the window start.
     - Only set is_movable=False if the task is pinned to an exact, immovable start time (e.g. "at 2pm sharp", "from 9am to 10:45am").
4. Time & Date Disambiguation:
   - Contextual AM/PM: If a start time lacks an AM/PM marker (e.g. 'from 10:45 to 12:15am'), infer AM/PM from context. If an event ends at 12:15 AM (midnight), the preceding 10:45 is 10:45 PM. Also, if a time without AM/PM would be in the past compared to Current Time, interpret it as the upcoming future hour.
   - Midnight Rollover: 12:00 AM (00:00) through early morning is part of the NEXT calendar day. If a task starts in the late evening of {current_date} (e.g. 10:45 PM) and ends past midnight (e.g. 12:15 AM or 1:00 AM), its 'end' datetime MUST be on the next calendar day ({next_date}T00:15 or {next_date}T01:00). An end datetime must never be earlier than its start datetime.
   - 12 AM vs 12 PM: 12:00 AM is 00:00 (midnight). 12:15 AM is 00:15. 12:00 PM is 12:00 (midday).
5. Preference & Slot Guidelines:
   - Do not greedily follow preference weights over actual user intent.
   - Do not anchor or constrain events into slots that are too low in preference weights or unsuitable, unless there is no choice or an explicit deadline demands it.

Output format:
Return a JSON object conforming strictly to the ScoredTaskList schema.
"""

SCORING_PROMPT_TEMPLATE = """You are a deterministic task prioritization and scoring engine for an autonomous personal calendar scheduler.

You will receive an array of extracted calendar items originating from unstructured notes.
Your task is to evaluate, calibrate, and augment each item with operational scheduling scores, realistic duration estimates, and mobility constraints.

### Reference Anchors & Context
- Current Reference Date: {current_date}
- Current Timezone: {timezone}

### Scoring & Attribute Rules
1. **importance (integer, 1 to 5)**:
   - 5: Critical academic or professional deliverables, exams, graded evaluations, high-stakes external meetings.
   - 4: Major goal progress, core project development, essential preparations.
   - 3: Standard routine tasks, personal habit maintenance, self-study sessions.
   - 2: Low-impact personal chores, entertainment, casual content creation.
   - 1: Purely optional, trivial, or exploratory items.

2. **urgency (integer, 1 to 5)**:
   - 5: Explicitly due today, happening within next 12 hours, or marked with immediate keywords ("ASAP").
   - 4: Hard deadline within next 24 to 48 hours.
   - 3: Due within next 3 to 7 days, or scheduled for a specific upcoming day this week.
   - 2: Flexible deadline beyond 7 days.
   - 1: No fixed deadline, backlog idea, or "someday/maybe" task.

3. **estimated_duration (integer, in minutes)**:
   - If explicit in input, preserve it.
   - Otherwise infer: lecture=60/120, meeting=30/60, evaluative=60/120, routine=30/60, others=15/30.
   - Must strictly be a positive integer >= 1.

4. **is_movable (boolean)**:
   - false: Fixed-time commitments involving other people or fixed classes.
   - true: Self-directed activities, solo study, practice, reports, chores.

5. **priority_category (string enum)**:
   - "urgent": importance >= 4 AND urgency >= 4
   - "important": importance >= 4 AND urgency < 4
   - "routine": event_type == "routine" OR (importance == 3 AND urgency <= 3)
   - "low_priority": importance <= 2

6. **scoring_rationale (string)**:
   - One concise sentence explaining why the scores and mobility were assigned.

7. **Preference & Slot Guidelines**:
   - Do not greedily follow preference weights.
   - Do not recommend or anchor events in slots too low in preference weights unless there is no alternative choice.

8. **High-Priority Task Protection & All-Day Milestones**:
   - High-priority tasks (academic evaluations, vivas, exams, presentations, critical deliverables) must be scored with high importance (4-5) and urgency (4-5) and prioritized so that lower-priority tasks (general help, casual study, routine chores) never take up their slot or window.
   - If a quiz, exam, presentation, viva, or similar super important task/milestone is present in the task list with only a date and no specific time, set its "start" datetime anchor to 00:00:00 (YYYY-MM-DDT00:00:00) and its "end" datetime anchor to 23:59:59 (YYYY-MM-DDT23:59:59) and mark is_movable=true so it can be scheduled within daytime operating hours on that date.
   - Unless an explicit future date is attached, preparation tasks (e.g. 'quiz prep', 'study session') are meant for today. Never assign deadlines in the past.

### Input Data
{response_json}
"""

CURVE_EVOLUTION_PROMPT_TEMPLATE = """You are an adaptive preference calibration engine for an autonomous personal calendar scheduler.

The user has scheduled/confirmed a calendar event. Your task is to inspect the event's nature and timing, take the previous 24-hour hourly preference weights, and output the updated 24-hour preference curve.

### Event Information
- Title: {task_title}
- Event Type: {event_type}
- Scheduled Start: {start_time}
- Scheduled End: {end_time}
- Duration: {duration_minutes} minutes

### Current Preference State
- Observed Placement Count: {updated_count}
- Current Hourly Weights (Hours 00:00 to 23:00):
{current_weights_json}

### Calibration Instructions
1. **Analyze Nature of Task & Time-of-Day Feasibility**:
   - Consider physical, acoustic, and social constraints (e.g., loud music practice, outdoor workouts, deep solitary coding, external calls).
   - If this is a first-time observation (`updated_count == 0`), establish a coherent baseline shape:
     - Suppress infeasible/disruptive hours (e.g. night hours 23:00–08:00 for loud instruments or calls).
     - Boost the scheduled hour window significantly (e.g., 0.8 to 1.0).
     - Provide a smooth transition to adjacent hours.
   - If this is a recurring observation (`updated_count > 0`), reinforce the hours where the event occurred with an incremental boost while preserving previously learned shapes.
2. **Preference Curve Calibration Principles**:
   - Do not greedily polarize weights (e.g. driving viable daytime hours to near zero).
   - Maintain moderate, viable baseline weights across usable daytime hours so the scheduler does not greedily over-index on a single hour.
   - Keep truly unfavorable or disruptive slots sufficiently low in weight so the optimizer will not put events into slots too low in weights unless there is no choice.
3. **Strict Range Requirements**:
   - Every single hourly weight in the output list MUST strictly be a float between 0.0 and 1.0.
   - Output must contain EXACTLY 24 floats representing hours 0 through 23 in chronological order.
4. **Format**:
   - Return a JSON object strictly conforming to the UpdatedCurveResponse schema:
     - "hourly_weights": array of exactly 24 floats.
     - "update_rationale": one concise sentence explaining how the curve was adjusted.
"""


def build_scoring_prompt(raw_json_text: str, current_date: str, timezone: str) -> str:
    """Builds scoring prompt avoiding str.format brace clashes."""
    return (
        SCORING_PROMPT_TEMPLATE
        .replace("{current_date}", current_date)
        .replace("{timezone}", timezone)
        .replace("{response_json}", raw_json_text)
    )


def build_notes_extraction_prompt(
    raw_notes_text: str,
    current_date: str,
    timezone: str,
    current_time: Optional[str] = None,
    next_date: Optional[str] = None,
) -> str:
    """Builds raw notes extraction prompt avoiding str.format brace clashes."""
    import datetime as _dt
    if next_date is None:
        try:
            d = _dt.date.fromisoformat(current_date)
            next_date = (d + _dt.timedelta(days=1)).isoformat()
        except Exception:
            next_date = current_date
    if current_time is None:
        current_time = "Unspecified"

    return (
        NOTES_EXTRACTION_PROMPT_TEMPLATE
        .replace("{current_date}", current_date)
        .replace("{next_date}", next_date)
        .replace("{current_time}", current_time)
        .replace("{timezone}", timezone)
        .replace("{raw_notes}", raw_notes_text)
    )


def build_curve_evolution_prompt(
    task_title: str,
    event_type: str,
    start_time: str,
    end_time: str,
    duration_minutes: int,
    current_weights_json: str,
    updated_count: int,
) -> str:
    """Builds curve evolution prompt avoiding str.format brace clashes."""
    return (
        CURVE_EVOLUTION_PROMPT_TEMPLATE
        .replace("{task_title}", task_title)
        .replace("{event_type}", event_type)
        .replace("{start_time}", start_time)
        .replace("{end_time}", end_time)
        .replace("{duration_minutes}", str(duration_minutes))
        .replace("{current_weights_json}", current_weights_json)
        .replace("{updated_count}", str(updated_count))
    )


TIME_FINDER_PROMPT_TEMPLATE = """You are an intelligent scheduling horizon analyzer for an autonomous calendar system.

### Context & Anchors
- Current Reference Datetime (tz.now): {current_datetime}
- Current Date: {current_date}
- Current Timezone: {timezone}

### Input Structured Notes / Tasks JSON
\"\"\"
{structured_notes_json}
\"\"\"

### Instructions
1. Analyze all tasks, events, and milestones in the input structured notes JSON.
2. Identify all calendar dates mentioned either explicitly in start/end anchors (e.g., "dateTime": "YYYY-MM-DDTHH:MM" or "00:00:00"/"23:59:59"), or embedded in task titles/descriptions (such as "17th oct", "sp2 quiz on 23rd oct", "tomorrow", etc.). Note that quizzes, exams, or presentations with only dates may have start 00:00:00 and end 23:59:59.
3. Determine:
   - "start_datetime": The scheduling start boundary in ISO 8601 format with timezone offset. Usually this is current reference datetime ({current_datetime}) so scheduling begins from now.
   - "end_datetime": Find the latest date among all tasks/events in the notes. Set "end_datetime" strictly to that latest date's End of Day (EOD) at 23:59:59 in the active timezone (for example: if notes mention an event on 17th oct and another event on 23rd oct, the latest date is 23rd oct, so end_datetime is 23rd oct at 23:59:59, e.g. YYYY-10-23T23:59:59+05:30).
   - If no future date beyond today is mentioned, or notes only contain tasks for today, set "end_datetime" to today's End of Day ({current_date}T23:59:59+05:30).
   - "latest_event_title": Title or description of the latest task/event that determined the horizon end date.
   - "reasoning": Concise explanation of the identified dates.

Ensure the output conforms strictly to the TimeHorizonOutput JSON schema.
"""


def build_time_finder_prompt(
    structured_notes_json: str,
    current_datetime: str,
    current_date: str,
    timezone: str,
) -> str:
    """Builds time finder prompt avoiding str.format brace clashes."""
    return (
        TIME_FINDER_PROMPT_TEMPLATE
        .replace("{current_datetime}", current_datetime)
        .replace("{current_date}", current_date)
        .replace("{timezone}", timezone)
        .replace("{structured_notes_json}", structured_notes_json)
    )

