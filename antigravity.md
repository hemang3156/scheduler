# antigravity.md — Autonomous Calendar Scheduling Agent

Permanent project context, architecture blueprint, and technical conventions for the Autonomous Calendar Scheduling Agent.

---

## 1. Project Vision & Identity

This project transitions the scheduler from an exploratory, one-way batch pipeline (Notebooks $\rightarrow$ CSV) into an **Autonomous, Tool-Calling Executive Assistant** built on production AI Engineering principles ([roadmap.sh/ai-engineer](https://roadmap.sh/ai-engineer)).

The agent ingests messy natural language notes, parses them into schema-validated task drafts, resolves scheduling conflicts around immutable calendar blocks using deterministic gap-finding and weighted priority scoring, and presents an interactive schedule diff before syncing to Google Calendar.

---

## 2. System Architecture

```
                       User Input
             (CLI / Telegram / Notes / Email)
                           │
                           ▼
            ┌─────────────────────────────┐
            │   Interface Layer (CLI/API) │
            └──────────────┬──────────────┘
                           ▼
          ┌──────────────────────────────────┐
          │     Executive Agent (LLM)        │
          │  • Intent & Priority Extraction  │
          │  • Constraint & Memory Check     │
          │  • ReAct Tool-Calling Loop       │
          └────┬──────────────┬──────────────┘
               │              │
    ┌──────────▼──────┐   ┌───▼───────────────────────────┐
    │ Preferences RAG │   │      Deterministic Tools      │
    ├─────────────────┤   ├───────────────────────────────┤
    │ User habits,    │   │ • CalendarClient (OAuth2 SDK) │
    │ routines, focus │   │ • SlotFinder (Gap Algorithm)  │
    │ blocks, people  │   │ • PlacementScorer (Math)      │
    └─────────────────┘   └───────────────┬───────────────┘
                                          │
                                          ▼
                             ┌─────────────────────────┐
                             │ Google Calendar API v3  │
                             └─────────────────────────┘
```

---

## 3. Directory Layout

```
scheduler/
├── src/
│   ├── core/                  # Pure Domain Logic (No external SDK dependencies)
│   │   ├── models.py          # CalendarEvent, TaskDraft, TimeSlot
│   │   ├── exceptions.py      # CalendarError, CalendarAuthError, CalendarResourceNotFoundError
│   │   ├── scoring.py         # Priority & urgency weight calculation
│   │   └── optimizer.py       # Deterministic task placement & gap fitting
│   │
│   ├── tools/                 # External Adapters & Tool Implementations
│   │   ├── calender_client.py # Google Calendar API v3 client (OAuth2, CRUD)
│   │   └── slot_finder.py     # Pure gap-finding & interval-merging algorithm
│   │
│   ├── agent/                 # LLM Orchestration
│   │   ├── prompt_templates.py# System prompts, role instructions, few-shots
│   │   └── scheduler_agent.py # Gemini tool-calling loop with structured outputs
│   │
│   └── interfaces/            # Delivery & Presentation Layer
│       └── cli.py             # Terminal interface with schedule diff preview [Y/n]
│
├── tests/
│   ├── conftest.py            # Reusable pytest fixtures (mock events, test dates)
│   ├── test_calendar_tools.py # Offline unit tests for slot_finder and models
│   └── test_agent_evals.py    # Regression tests evaluating LLM output quality
│
├── sceduler_refer_resources/  # Extracted print-ready study chapters for self-learning
│   └── week 1/
│       ├── Python_Data_Class_Builders.pdf
│       ├── Python_Cookbook_Chapter_3_Dates_and_Times.pdf
│       ├── Python_Cookbook_Chapter_14_Exception_Handling.pdf
│       ├── API_Client_Design_and_Error_Handling.pdf
│       ├── pytest_Chapter_2_Writing_Test_Functions.pdf
│       ├── pytest_Chapter_3_pytest_Fixtures.pdf
│       └── Fluent_Python_Chapter_1_The_Python_Data_Model.pdf
│
├── data/                      # Historical datasets, prompts, and sample notes
├── credentials.json           # Google Cloud OAuth client secret (Gitignored)
├── token.json                 # Google OAuth user access/refresh token (Gitignored)
├── requirements.txt
└── antigravity.md             # This project blueprint
```

---

## 4. Phased Roadmap & Progress Status

### Phase 1: Core Engine & Deterministic Tools (Completed)
- [x] **Step 1: Data Modeling (`src/core/models.py`)** — `CalendarEvent`, `TaskDraft`, `TimeSlot`.
- [x] **Step 2: Calendar Client (`src/tools/calender_client.py`)** — `CalendarClient` with OAuth2 token auto-refresh, `list_events`, `create_event`, and `patch_event`.
- [x] **Step 3: Gap Finder (`src/tools/gap_finder.py`)** — Interval merging, timeline cursor gap-calculation.
- [x] **Step 4: Unit Test Suite (`tests/`)** — Offline `pytest` suite with fixtures and parameterized tests (18 tests passing).

### Phase 2: Priority Placement & Agent Orchestration (Completed)
- [x] **Step 2.1: Priority Scoring Engine (`src/core/scoring.py` & `src/agent/scorer.py`)** — Formula $w_1 \cdot \text{importance} + w_2 \cdot \text{urgency} + w_3 \cdot (\text{importance} \times \text{urgency})$, Gemini structured output ingestion, resilient model fallbacks (`gemini-3.5-flash`, `gemini-3.8-flash`), and unit tests.
- [x] **Step 2.2: Task-to-Slot Optimizer (`src/core/optimizer.py`)** — Deterministic greedy interval placement, deadline verification, fixed-task slot carving, and buffer deduction.
- [x] **Step 2.3: Prompt Templates (`src/agent/prompt_templates.py`)** — Clean template builders for raw notes extraction and structured task scoring avoiding `str.format` brace collisions.
- [x] **Step 2.4: Agent Orchestration & Interactive CLI (`src/agent/scheduler_agent.py` & `src/interfaces/cli.py`)** — `AutonomousScheduler`, deterministic schedule diff formatter, human-in-the-loop sync confirmation `[y/N]`, and CLI flags. Total 33 offline unit tests passing in 0.60s.

### Phase 3: Preferences & Gradient Weight Evolution (Completed)
- [x] **Step 3.1: Preference Profiles & Baseline Engine (`src/core/models.py` & `src/core/preference_engine.py`)** — `PreferenceProfile`, `UpdatedCurveResponse`, profile loading, sanitizing, and weighted aggregate `recompute_global_baseline()`.
- [x] **Step 3.2: LLM Weight Evolution Engine (`src/agent/curve_updater.py`)** — `evolve_curve_with_llm()` takes current weights + event info, calls Gemini to adjust the 24-hour curve, and saves to disk.
- [x] **Step 3.3: Gradient-Aware Optimizer (`src/core/optimizer.py`)** — Continuous 24-hour willingness curve scoring ($W[h]$) combined with anchor proximity, slot carving, and buffer deduction.
- [x] **Step 3.4: Complete Unit Test Suite (`tests/`)** — 43 unit tests passing in 0.85s verifying models, gap finder, scoring, optimizer, preference engine, and curve evolution.

### Phase 4: Delivery, Safety Guardrails & Regression Evals (Completed)
- [x] **Interactive CLI (`src/interfaces/cli.py`)** — Diff table preview with human-in-the-loop `[y/N]` confirmation and `--raw-text` parsing.
- [x] **Automated Regression Evaluation Suite (`tests/test_evals.py` & `tests/data/synthetic_notes_dataset.json`)** — 16 synthetic edge cases measuring type accuracy (93.8%), mobility accuracy (100%), duration accuracy (100%), and anchor accuracy (100%).
- [x] **Test Isolation Architecture** — 43 deterministic unit tests execute offline in 1.8s (`pytest -m "not evals"`); live LLM benchmarks execute via `pytest tests/test_evals.py`.

---

## 5. Core Data Contracts (Hard Schemas)

### `CalendarEvent`
Represents an existing commitment on the calendar.
* `id: str`
* `summary: str`
* `start: datetime` (timezone-aware)
* `end: datetime` (timezone-aware)
* `is_all_day: bool`

### `TimeSlot`
Represents an open gap available for scheduling.
* `start: datetime` (timezone-aware)
* `end: datetime` (timezone-aware)
* `duration_minutes: int`

### `TaskDraft`
Represents an unscheduled task extracted from user notes.
* `title: str`
* `importance: int` (1 to 5)
* `urgency: int` (1 to 5)
* `estimated_duration: Optional[int]` (in minutes, or None if unspecified)
* `is_movable: bool`

---

## 6. Execution Commands & Environment

Project dependencies are run with `uv`:

```bash
# Set Python path to project root
export PYTHONPATH=.

# Test Calendar Client live connection
PYTHONPATH=. uv run --with google-api-python-client --with google-auth-oauthlib python3 -c "
import datetime as dt
from zoneinfo import ZoneInfo
from src.tools.calender_client import CalendarClient

client = CalendarClient()
tz = ZoneInfo('Asia/Kolkata')
now = dt.datetime.now(tz)
events = client.list_events(now, now + dt.timedelta(days=7))
print(f'Retrieved {len(events)} events.')
"

# Run offline unit tests
PYTHONPATH=. uv run --with pytest pytest tests/
```

---

## 7. Hard Engineering Rules & Invariants

1. **Decoupled Architecture**: Domain modules in `src/core/` must **never** import `googleapiclient`, Google credentials, or LLM libraries. External interactions live strictly in `src/tools/` or `src/agent/`.
2. **Timezone Uniformity**: All datetime calculations and ISO timestamps assume `Asia/Kolkata` (`ZoneInfo('Asia/Kolkata')`). Never use naive datetimes.
3. **No Silent Swallowing of Errors**: Never use bare `except:` or return `None` on errors. Translate API errors into custom domain exceptions (`CalendarError`, `CalendarResourceNotFoundError`) with exception chaining (`raise ... from error`).
4. **Human-in-the-Loop Safety**: The agent must never perform destructive updates or bulk schedule inserts without presenting a schedule diff and obtaining explicit user confirmation.
5. **Secrets Hygiene**: `credentials.json`, `token.json`, and API keys must remain strictly gitignored and never committed to version control.
6. **Markdown Math Syntax**: Always format mathematical equations using single dollar signs (`$...$`) for compatibility with MarkText.
