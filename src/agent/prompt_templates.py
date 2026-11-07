"""
Prompt templates for unstructured notes extraction, task prioritization, and preference curve evolution.
"""

NOTES_EXTRACTION_PROMPT_TEMPLATE = """You are an expert executive task extraction and scoring engine for an autonomous personal calendar scheduler.

Reference Anchors:
- Current Reference Date: {current_date}
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
   - "start": An explicit ISO local datetime (YYYY-MM-DDTHH:MM) if specifically scheduled, otherwise null.
   - "end": An explicit ISO local deadline or end datetime (YYYY-MM-DDTHH:MM) if stated, otherwise null.
   - "estimated_duration": Realistic duration in minutes (positive integer, minimum 15 minutes unless trivial).
   - "importance": Integer from 1 (trivial) to 5 (critical deliverable/health/high-stakes).
   - "urgency": Integer from 1 (flexible/someday) to 5 (due today/ASAP).
   - "is_movable": False for fixed meetings or scheduled classes with others; True for solo work, practice, chores.
   - "priority_category": "urgent", "important", "routine", or "low_priority".
   - "scoring_rationale": One concise sentence explaining the scores.

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
2. **Strict Range Requirements**:
   - Every single hourly weight in the output list MUST strictly be a float between 0.0 and 1.0.
   - Output must contain EXACTLY 24 floats representing hours 0 through 23 in chronological order.
3. **Format**:
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


def build_notes_extraction_prompt(raw_notes_text: str, current_date: str, timezone: str) -> str:
    """Builds raw notes extraction prompt avoiding str.format brace clashes."""
    return (
        NOTES_EXTRACTION_PROMPT_TEMPLATE
        .replace("{current_date}", current_date)
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
