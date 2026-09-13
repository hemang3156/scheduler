# Autonomous Task & Calendar Scheduler

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Google Gemini API](https://img.shields.io/badge/LLM-Google%20Gemini%20API-4285F4?style=flat-square&logo=google&logoColor=white)](https://ai.google.dev/)
[![Google Calendar API](https://img.shields.io/badge/Integration-Google%20Calendar%20API-34A853?style=flat-square&logo=google-calendar&logoColor=white)](https://developers.google.com/calendar)
[![OAuth 2.0](https://img.shields.io/badge/Auth-OAuth%202.0-orange?style=flat-square)](https://oauth.net/2/)
[![Pandas](https://img.shields.io/badge/Data-Pandas-150458?style=flat-square&logo=pandas&logoColor=white)](https://pandas.pydata.org/)

An intelligent automated personal scheduling assistant integrating **Google Gemini LLM** and the **Google Calendar API** to ingest unstructured natural language notes, parse them into strict schema-validated event and task records, classify priority and urgency, and programmatically merge them around immutable calendar commitments.

---

## 🏗️ Architecture & Pipeline Flow

```mermaid
flowchart TD
    subgraph Ingestion ["1. Multi-Modal Ingestion"]
        A[Google Calendar Ingestion\n(API.py / OAuth 2.0)] --> C[Normalized Calendar Events\nCSV_1]
        B[Informal Notes & Tasks\n(Markdown / Plaintext)] --> D[Prompt Template +\nGemini LLM Extraction]
    end

    subgraph Structuring ["2. Structured Parsing & Validation"]
        D --> E[JSON Schema Validation\ntitle, event_type, start, end, duration, importance, urgency]
        E --> F[Deterministic Fallback Extractor\n(notes_extractor.py)]
        F --> G[Extracted Task Records\nCSV_2]
    end

    subgraph Optimization ["3. Conflict Resolution & Merging"]
        C & G --> H[Conflict Resolution Engine]
        H --> I{Is Task Movable?}
        I -- Locked / Fixed --> J[Anchor to Strict Time Slot]
        I -- Movable --> K[Fit Around Immutable Calendar Blocks]
    end

    subgraph Execution ["4. Calendar Synchronization"]
        J & K --> L[Schedule Review & Verification]
        L --> M[Write Back to Google Calendar API]
    end
```

---

## ✨ Core Features

1. **Natural Language Notes Parsing:** Extracts tasks, lectures, meetings, routines, and evaluative deadlines from conversational, ambiguous notes using structured Gemini prompts.
2. **Deterministic Fallback Pipeline:** Includes standalone rule-based extraction (`notes_extractor.py`) to parse anchor times, duration estimates, and lock statuses without requiring network calls or LLM tokens during local testing.
3. **Multi-Dimensional Prioritization:** Evaluates tasks across **Importance (1–5)** and **Urgency (1–5)** metrics, incorporating weighted ranking formulas ($w_1 \cdot \text{importance} + w_2 \cdot \text{urgency} + w_3 \cdot [\text{importance} \times \text{urgency}]$) for schedule placement.
4. **Bidirectional Calendar Sync:** Authenticates securely via OAuth 2.0 to fetch real-time calendar agendas and post optimized schedules back to Google Calendar.
5. **Architectural Blueprint:** Detailed pipeline milestone logs and design specifications are documented in [`progress_report.md`](progress_report.md) and [`names,readme,notes/`](names,readme,notes/).

---

## 📂 Repository Layout

- `API.py`: Google Calendar API authentication, event retrieval, and event creation service.
- `notes_extractor.py`: Deterministic rule-based extraction baseline for offline testing.
- `data_processing.ipynb`: Notebook for importing, cleaning, and normalizing calendar events.
- `notes_to_data.ipynb`: Notebook for LLM-backed notes extraction and DataFrame export.
- `email_forwarder.py`: Starred email ingestion and task forwarding utility with rate-limiting and backoff.
- `data/import/`: Source notes, prompts (`extraction_prompt_template.md`), and expected extraction samples.
- `names,readme,notes/`: Design diagrams, phase specifications, and classifier notes.

---

## 🚀 Setup & Execution

### 1. Environment Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt  # or install google-api-python-client google-auth-httplib2 google-auth-oauthlib pandas
```

### 2. Google Calendar OAuth
Place your Google Cloud OAuth client secret in `credentials.json` (gitignored). On the first run:
```bash
python API.py
```
This launches a local OAuth flow and saves your authorized token to `token.json`.

### 3. Run Offline Deterministic Extractor
```bash
python notes_extractor.py "names,readme,notes/Phase 2(sample notes).md" --reference-date 2026-07-16
```

### 4. Run Unit Tests
```bash
python -m unittest discover -s tests -v
```

---

## 📄 License & Attribution
Designed and engineered by [Hemang Garg](https://github.com/hemang3156).
