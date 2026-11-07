import argparse
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from src.agent.scorer import score_tasks_from_json, extract_and_score_notes
from src.agent.scheduler_agent import AutonomousScheduler, format_schedule_diff
from src.tools.calender_client import CalendarClient
from src.core.exceptions import CalendarAuthError, CalendarError

TIMEZONE = "Asia/Kolkata"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Autonomous Calendar Scheduling Agent — Ingests informal notes, optimizes placement, and syncs to Google Calendar."
    )
    parser.add_argument(
        "note_words",
        nargs="*",
        default=None,
        help="Direct note input as command-line arguments (e.g. scheduler Guitar practice 8pm).",
    )
    parser.add_argument(
        "-n",
        "--notes",
        type=str,
        default=None,
        help="Path to markdown/text file of informal notes or response.json (defaults to data/daily_notes.md if present).",
    )
    parser.add_argument(
        "--raw-text",
        type=str,
        default=None,
        help="Raw text string of unstructured notes directly from the command line.",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target date for scheduling (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--tomorrow",
        action="store_true",
        help="Schedule tasks for tomorrow instead of today.",
    )
    parser.add_argument(
        "--start-hour",
        type=int,
        default=9,
        help="Daily starting hour in 24-hour format (default: 9 for 09:00).",
    )
    parser.add_argument(
        "--end-hour",
        type=int,
        default=21,
        help="Daily ending hour in 24-hour format (default: 21 for 21:00).",
    )
    parser.add_argument(
        "--buffer",
        type=int,
        default=5,
        help="Buffer minutes between scheduled tasks (default: 5).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without connecting to Google Calendar; outputs schedule diff preview only.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Prompt to commit and write placed tasks into Google Calendar.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    tz = ZoneInfo(TIMEZONE)
    now = dt.datetime.now(tz)

    if args.tomorrow:
        target_date = (now + dt.timedelta(days=1)).date()
    elif args.date:
        try:
            target_date = dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Expected YYYY-MM-DD.")
            sys.exit(1)
    else:
        target_date = now.date()

    start_window = dt.datetime.combine(target_date, dt.time(args.start_hour, 0), tzinfo=tz)
    end_window = dt.datetime.combine(target_date, dt.time(args.end_hour, 0), tzinfo=tz)

    if start_window >= end_window:
        print(f"Error: Start window ({start_window.strftime('%H:%M')}) must be earlier than end window ({end_window.strftime('%H:%M')}).")
        sys.exit(1)

    print("\n" + "=" * 80)
    print("      AUTONOMOUS CALENDAR SCHEDULER — INITIALIZING ENGINE")
    print("=" * 80)
    print(f"Timezone:          {TIMEZONE}")
    print(f"Target Date:       {target_date.isoformat()} ({'Tomorrow' if args.tomorrow else 'Today'})")
    print(f"Scheduling Window: {start_window.strftime('%Y-%m-%d %H:%M')} -> {end_window.strftime('%H:%M')}")
    print(f"Task Buffer:       {args.buffer} minutes")

    # Step 1: Ingest & Score Tasks
    print("\n[Step 1/3] Extracting and prioritizing tasks via Gemini API...")
    try:
        if args.raw_text:
            print(" -> Parsing raw notes text from --raw-text flag...")
            tasks = extract_and_score_notes(args.raw_text)
        elif args.note_words and len(args.note_words) > 0:
            raw_input = " ".join(args.note_words).strip()
            print(f" -> Parsing direct command-line note: \"{raw_input}\"...")
            tasks = extract_and_score_notes(raw_input)
        elif args.notes:
            notes_path = Path(args.notes)
            if not notes_path.exists():
                print(f"Error: File not found: {notes_path}")
                sys.exit(1)
            if notes_path.suffix == ".json":
                print(f" -> Parsing JSON task drafts from {notes_path}...")
                tasks = score_tasks_from_json(response_json_path=notes_path)
            else:
                print(f" -> Parsing text notes from {notes_path}...")
                tasks = extract_and_score_notes(notes_path.read_text(encoding="utf-8"))
        else:
            repo_root = Path(__file__).resolve().parent.parent.parent
            daily_notes_path = repo_root / "data" / "daily_notes.md"
            if daily_notes_path.exists():
                print(f" -> Ingesting active daily notes from {daily_notes_path}...")
                tasks = extract_and_score_notes(daily_notes_path.read_text(encoding="utf-8"))
            else:
                fallback_path = repo_root / "redundant" / "export" / "response.json"
                if fallback_path.exists():
                    print(f" -> Ingesting fallback tasks from {fallback_path}...")
                    tasks = score_tasks_from_json(response_json_path=fallback_path)
                else:
                    print("Error: No note input provided. Pass a note via arguments or write to data/daily_notes.md.")
                    sys.exit(1)
        print(f" -> Successfully scored {len(tasks)} tasks.")
    except Exception as e:
        print(f"Task Scoring Error: {e}")
        sys.exit(1)

    # Step 2: Initialize Calendar & Find Free Slots
    print("\n[Step 2/3] Fetching commitments & optimizing schedule plan...")
    client = None
    existing_events = []

    if not args.dry_run:
        try:
            client = CalendarClient()
            existing_events = client.list_events(start_window, end_window)
            print(f" -> Retrieved {len(existing_events)} existing calendar commitments.")
        except (CalendarAuthError, CalendarError) as e:
            print(f" -> Calendar connection warning: {e}")
            print(" -> Falling back to offline dry-run mode (no external events).")
            client = None
    else:
        print(" -> Dry-run mode enabled: skipping live Google Calendar fetch.")

    scheduler = AutonomousScheduler(calendar_client=client)
    plan = scheduler.generate_plan(
        tasks=tasks,
        start_window=start_window,
        end_window=end_window,
        existing_events=existing_events,
        buffer_minutes=args.buffer,
    )

    # Step 3: Render Diff
    print("\n[Step 3/3] Optimization complete. Reviewing proposed schedule diff:")
    diff_text = format_schedule_diff(plan)
    print("\n" + diff_text)

    # Step 4: Interactive Confirmation
    if args.sync and client is not None and plan.placed_tasks:
        print("\n" + "-" * 80)
        confirm = input("Would you like to sync these placed tasks to Google Calendar? [y/N]: ").strip().lower()
        if confirm == "y":
            print("Syncing events to Google Calendar...")
            created = scheduler.apply_plan(plan, dry_run=False)
            print(f"Successfully synced {len(created)} events to your calendar!")
        else:
            print("Sync aborted by user. No calendar changes were made.")
    else:
        print("\nSchedule generation complete. Run with --sync to write to Google Calendar.")


if __name__ == "__main__":
    main()
