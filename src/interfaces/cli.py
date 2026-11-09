import argparse
import datetime as dt
import sys
import textwrap
from pathlib import Path
from zoneinfo import ZoneInfo

from src.agent.scorer import score_tasks_from_json, extract_and_score_notes
from src.agent.scheduler_agent import AutonomousScheduler, format_schedule_diff
from src.tools.calender_client import CalendarClient
from src.core.exceptions import CalendarAuthError, CalendarError

TIMEZONE = "Asia/Kolkata"

# ── ANSI helpers ──────────────────────────────────────────────────────
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"


def _banner():
    print(f"""
{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗
║         📅  AUTONOMOUS CALENDAR SCHEDULER  📅               ║
╚══════════════════════════════════════════════════════════════╝{RESET}
""")


def _ask(prompt: str, default: str = "") -> str:
    """Ask a question with an optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{GREEN}▸ {prompt}{suffix}: {RESET}").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)
    return answer if answer else default


def _ask_choice(prompt: str, options: list[str], default: int = 1) -> str:
    """Present numbered options and return the chosen value."""
    print(f"\n{GREEN}▸ {prompt}{RESET}")
    for i, opt in enumerate(options, 1):
        marker = f"{BOLD}→{RESET}" if i == default else " "
        print(f"  {marker} {i}. {opt}")
    while True:
        try:
            raw = input(f"{DIM}  Enter choice [default {default}]: {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if not raw:
            return options[default - 1]
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        except ValueError:
            pass
        print(f"  {RED}Please enter a number between 1 and {len(options)}.{RESET}")


def _ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    """Ask a yes/no question."""
    hint = "Y/n" if default_yes else "y/N"
    try:
        raw = input(f"{GREEN}▸ {prompt} [{hint}]: {RESET}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)
    if not raw:
        return default_yes
    return raw in ("y", "yes")


def _ask_multiline(prompt: str) -> str:
    """Collect multi-line input. End with an empty line."""
    print(f"\n{GREEN}▸ {prompt}{RESET}")
    print(f"{DIM}  (Type each task on a new line. Press Enter on an empty line when done.){RESET}\n")
    lines = []
    while True:
        try:
            line = input(f"  {CYAN}>{RESET} ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def interactive_mode(now: dt.datetime | None = None):
    """Guided wizard for non-technical users. No flags, no jargon."""
    _banner()
    tz = ZoneInfo(TIMEZONE)
    if now is None:
        now = dt.datetime.now(tz).replace(microsecond=0)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)

    # ── Step 1: Select date or date range ────────────────────────────
    date_mode = _ask_choice(
        "Select scheduling date or date range:",
        [
            "Date range (choose initial start date and final end date)",
            f"Today only ({now.strftime('%A, %b %d')})",
            f"Tomorrow only ({(now + dt.timedelta(days=1)).strftime('%A, %b %d')})",
            "Specific single date",
        ],
        default=1,
    )

    if date_mode.startswith("Date range"):
        while True:
            start_str = _ask("Initial (start) date (YYYY-MM-DD)", default=now.date().isoformat())
            try:
                initial_date = dt.date.fromisoformat(start_str)
                break
            except ValueError:
                print(f"  {RED}Invalid date format. Expected YYYY-MM-DD (e.g. {now.date().isoformat()}).{RESET}")

        while True:
            end_str = _ask("Final (end) date (YYYY-MM-DD)", default=initial_date.isoformat())
            try:
                final_date = dt.date.fromisoformat(end_str)
                if final_date < initial_date:
                    print(f"  {RED}Final date ({final_date}) cannot be earlier than initial date ({initial_date}).{RESET}")
                    continue
                break
            except ValueError:
                print(f"  {RED}Invalid date format. Expected YYYY-MM-DD (e.g. {initial_date.isoformat()}).{RESET}")

    elif date_mode.startswith("Today only"):
        initial_date = now.date()
        final_date = now.date()

    elif date_mode.startswith("Tomorrow only"):
        initial_date = (now + dt.timedelta(days=1)).date()
        final_date = (now + dt.timedelta(days=1)).date()

    else:
        while True:
            date_str = _ask("Enter date (YYYY-MM-DD)", default=now.date().isoformat())
            try:
                initial_date = dt.date.fromisoformat(date_str)
                final_date = initial_date
                break
            except ValueError:
                print(f"  {RED}Invalid date format. Expected YYYY-MM-DD (e.g. {now.date().isoformat()}).{RESET}")

    # ── Step 2: Time window ──────────────────────────────────────────
    use_defaults = _ask_yes_no(
        "Use default daily time window (09:00 – 21:00)?", default_yes=True
    )
    if use_defaults:
        start_hour, end_hour = 9, 21
    else:
        start_str = _ask("Daily start hour (0-23, e.g. 9 for 9 AM)", "9")
        end_str = _ask("Daily end hour (0-23, e.g. 21 for 9 PM)", "21")
        try:
            start_hour = int(start_str)
            end_hour = int(end_str)
            if not (0 <= start_hour < 24 and 0 <= end_hour <= 24 and start_hour < end_hour):
                raise ValueError()
        except ValueError:
            print(f"{RED}Invalid hours. Using defaults (09:00 – 21:00).{RESET}")
            start_hour, end_hour = 9, 21

    start_window = dt.datetime.combine(initial_date, dt.time(start_hour, 0), tzinfo=tz)
    end_window = dt.datetime.combine(final_date, dt.time(end_hour, 0), tzinfo=tz)

    if initial_date == now.date() and start_window < now:
        start_window = now

    if initial_date == final_date and start_window >= end_window:
        _error_exit(
            f"Start window ({start_window.strftime('%I:%M %p')}) is at or after end window ({end_window.strftime('%I:%M %p')}).",
            "Today's daily window has ended. Choose tomorrow or a future date range.",
        )
    elif initial_date < final_date and now >= dt.datetime.combine(initial_date, dt.time(end_hour, 0), tzinfo=tz):
        initial_date = initial_date + dt.timedelta(days=1)
        start_window = dt.datetime.combine(initial_date, dt.time(start_hour, 0), tzinfo=tz)

    # ── Step 3: Collect tasks ────────────────────────────────────────
    notes_text = _ask_multiline(
        "What do you want to schedule? Write your tasks in plain English"
    )
    if not notes_text.strip():
        print(f"\n{YELLOW}No tasks entered. Nothing to schedule.{RESET}")
        sys.exit(0)

    # ── Step 4: Preview or sync? ─────────────────────────────────────
    mode_choice = _ask_choice(
        "What would you like to do?",
        [
            "Preview only (don't touch my calendar)",
            "Preview and add to Google Calendar",
        ],
        default=1,
    )
    wants_sync = mode_choice.startswith("Preview and add")

    # ── Step 5: Run the engine ───────────────────────────────────────
    print(f"\n{'=' * 60}")
    if initial_date == final_date:
        print(f"  Scheduling for {BOLD}{initial_date.strftime('%A, %B %d, %Y')}{RESET}")
        print(f"  Window: {start_window.strftime('%I:%M %p')} → {end_window.strftime('%I:%M %p')}")
    else:
        days_count = (final_date - initial_date).days + 1
        print(f"  Scheduling Range: {BOLD}{initial_date.isoformat()} → {final_date.isoformat()} ({days_count} days){RESET}")
        print(f"  Daily Window:     {start_window.strftime('%I:%M %p')} → {end_window.strftime('%I:%M %p')} each day")
    print(f"{'=' * 60}")
    print(f"{'=' * 60}")

    # 5a. Extract & score
    print(f"\n{CYAN}[1/3]{RESET} Analyzing your tasks with AI...")
    try:
        tasks = extract_and_score_notes(notes_text)
        print(f"      Found {BOLD}{len(tasks)}{RESET} tasks.")
    except Exception as e:
        _error_exit(
            "Could not analyze your tasks.",
            "Make sure your Gemini API key is set up correctly.",
            detail=str(e),
        )

    # 5b. Calendar
    print(f"{CYAN}[2/3]{RESET} Checking your calendar for conflicts...")
    client = None
    existing_events = []
    if wants_sync:
        try:
            client = CalendarClient()
            existing_events = client.list_events(start_window, end_window)
            print(f"      Found {len(existing_events)} existing events on your calendar.")
        except (CalendarAuthError, CalendarError) as e:
            print(f"      {YELLOW}Could not connect to Google Calendar: {e}{RESET}")
            print(f"      {YELLOW}Falling back to preview-only mode.{RESET}")
            client = None
            wants_sync = False
    else:
        print(f"      {DIM}Preview mode — skipping calendar check.{RESET}")

    # 5c. Optimize
    print(f"{CYAN}[3/3]{RESET} Building your optimal schedule...")
    scheduler = AutonomousScheduler(calendar_client=client)
    plan = scheduler.generate_plan(
        tasks=tasks,
        start_window=start_window,
        end_window=end_window,
        existing_events=existing_events,
        buffer_minutes=5,
        now=now,
    )

    # ── Step 6: Show results ─────────────────────────────────────────
    diff_text = format_schedule_diff(plan)
    print(f"\n{diff_text}")

    # ── Step 7: Confirm sync ─────────────────────────────────────────
    if wants_sync and client is not None and plan.placed_tasks:
        do_sync = _ask_yes_no("Add these events to your Google Calendar?", default_yes=True)
        if do_sync:
            print(f"\n{CYAN}Syncing to Google Calendar...{RESET}")
            try:
                created = scheduler.apply_plan(plan, dry_run=False)
                print(f"\n{GREEN}{BOLD}✓ Done! {len(created)} events added to your calendar.{RESET}")
            except Exception as e:
                print(f"\n{RED}Error syncing: {e}{RESET}")
                print("Your calendar was not modified. Try again or run with preview mode.")
        else:
            print(f"\n{YELLOW}No changes made to your calendar.{RESET}")
    elif not plan.placed_tasks:
        print(f"\n{YELLOW}No tasks could be placed in the available time slots.{RESET}")
    else:
        print(f"\n{DIM}Preview complete. Run again and choose 'Preview and add' to sync to your calendar.{RESET}")

    print()


def _error_exit(message: str, hint: str = "", detail: str = ""):
    """Print a user-friendly error and exit."""
    print(f"\n{RED}{BOLD}✗ {message}{RESET}")
    if hint:
        print(f"  {YELLOW}{hint}{RESET}")
    if detail:
        print(f"  {DIM}Technical detail: {detail}{RESET}")
    sys.exit(1)


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
        "--start-date",
        "--from",
        dest="start_date",
        type=str,
        default=None,
        help="Initial date for scheduling range (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        "--to",
        dest="end_date",
        type=str,
        default=None,
        help="Final date for scheduling range (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--tomorrow",
        action="store_true",
        help="Schedule tasks for tomorrow instead of today.",
    )
    parser.add_argument(
        "--start-hour",
        type=int,
        default=None,
        help="Daily starting hour in 24-hour format (default: 9 for 09:00).",
    )
    parser.add_argument(
        "--end-hour",
        type=int,
        default=None,
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
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Automatically confirm and bypass [y/N] prompt when --sync is active.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Launch the guided interactive wizard (no flags needed).",
    )
    return parser.parse_args(argv)


def _should_use_interactive(args) -> bool:
    """Decide if we should launch interactive mode instead of the standard pipeline."""
    # Explicit --interactive flag always wins
    if args.interactive:
        return True
    # If user passed any explicit input source or flags, use standard mode
    if args.note_words and len(args.note_words) > 0:
        return False
    if args.notes or args.raw_text:
        return False
    if args.dry_run or args.sync or args.tomorrow or args.date or args.start_date or args.end_date:
        return False
    # No input and no daily_notes.md → launch interactive
    repo_root = Path(__file__).resolve().parent.parent.parent
    daily_notes_path = repo_root / "data" / "daily_notes.md"
    if daily_notes_path.exists():
        content = daily_notes_path.read_text(encoding="utf-8").strip()
        if content:
            return False
    return True


def main(argv=None, now: dt.datetime | None = None):
    args = parse_args(argv)

    # ── Interactive wizard shortcut ──────────────────────────────────
    if _should_use_interactive(args):
        interactive_mode(now=now)
        return

    tz = ZoneInfo(TIMEZONE)
    if now is None:
        now = dt.datetime.now(tz).replace(microsecond=0)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)

    is_default_range = False
    if args.tomorrow:
        initial_date = (now + dt.timedelta(days=1)).date()
        final_date = initial_date
    elif args.start_date or args.end_date:
        try:
            initial_date = dt.date.fromisoformat(args.start_date or args.date or now.date().isoformat())
            final_date = dt.date.fromisoformat(args.end_date or args.start_date or args.date or now.date().isoformat())
            if initial_date > final_date:
                _error_exit(
                    f"Initial date ({initial_date}) must be on or before final date ({final_date}).",
                    "Check your --start-date and --end-date values.",
                )
        except ValueError as ve:
            _error_exit(
                f"Invalid date format in date range.",
                "Expected format: YYYY-MM-DD (e.g., 2026-12-25).",
                detail=str(ve),
            )
    elif args.date:
        try:
            initial_date = dt.date.fromisoformat(args.date)
            final_date = initial_date
        except ValueError:
            _error_exit(
                f"Invalid date format '{args.date}'.",
                "Expected format: YYYY-MM-DD (e.g., 2026-12-25).",
            )
    else:
        # Default: from tz.now to 24 hours after it
        is_default_range = True
        initial_date = now.date()
        final_date = (now + dt.timedelta(hours=24)).date()

    if final_date < now.date():
        _error_exit(
            f"Target date ({final_date}) is in the past.",
            "Scheduling is only permitted for the current time onward.",
        )

    if initial_date < now.date():
        initial_date = now.date()

    if is_default_range and args.start_hour is None and args.end_hour is None:
        daily_start = None
        daily_end = None
        continuous_window = True
        start_window = now
        end_window = now + dt.timedelta(hours=24)
    else:
        continuous_window = False
        start_hour = args.start_hour if args.start_hour is not None else 9
        end_hour = args.end_hour if args.end_hour is not None else 21
        daily_start = dt.time(start_hour, 0)
        daily_end = dt.time(end_hour, 0)

        if is_default_range:
            start_window = now
            end_window = now + dt.timedelta(hours=24)
        else:
            start_window = dt.datetime.combine(initial_date, daily_start, tzinfo=tz)
            end_window = dt.datetime.combine(final_date, daily_end, tzinfo=tz)

            if initial_date == now.date() and start_window < now:
                start_window = now

            if initial_date == final_date and start_window >= end_window:
                _error_exit(
                    f"Start window ({start_window.strftime('%Y-%m-%d %I:%M %p')}) is at or after end window ({end_window.strftime('%I:%M %p')}).",
                    "Today's scheduling window has ended. Use --tomorrow to schedule for tomorrow, or specify a later --end-hour.",
                )
            elif initial_date < final_date and now >= dt.datetime.combine(initial_date, daily_end, tzinfo=tz):
                initial_date = initial_date + dt.timedelta(days=1)
                start_window = dt.datetime.combine(initial_date, daily_start, tzinfo=tz)

    print("\n" + "=" * 80)
    print("      AUTONOMOUS CALENDAR SCHEDULER — INITIALIZING ENGINE")
    print("=" * 80)
    print(f"Timezone:          {TIMEZONE}")
    if is_default_range:
        print(f"Target Window:     {start_window.strftime('%Y-%m-%d %I:%M %p')} -> {end_window.strftime('%Y-%m-%d %I:%M %p')} (Next 24 Hours)")
        if daily_start is not None and daily_end is not None:
            print(f"Daily Operating:   {daily_start.strftime('%I:%M %p')} -> {daily_end.strftime('%I:%M %p')}")
        else:
            print("Daily Operating:   Continuous 24-Hour Rolling Window")
    elif initial_date == final_date:
        print(f"Target Date:       {initial_date.isoformat()} ({'Tomorrow' if args.tomorrow else 'Today'})")
        print(f"Scheduling Window: {start_window.strftime('%Y-%m-%d %I:%M %p')} -> {end_window.strftime('%I:%M %p')}")
    else:
        days_count = (final_date - initial_date).days + 1
        print(f"Target Date Range: {initial_date.isoformat()} -> {final_date.isoformat()} ({days_count} days)")
        print(f"Daily Window:      {start_window.strftime('%I:%M %p')} -> {end_window.strftime('%I:%M %p')} each day")
    print(f"Task Buffer:       {args.buffer} minutes")

    # Step 1: Ingest & Score Tasks
    print("\n[Step 1/3] Extracting and prioritizing tasks via Gemini API...")
    try:
        if args.raw_text:
            print(" -> Parsing raw notes text from --raw-text flag...")
            tasks = extract_and_score_notes(args.raw_text)
        elif args.note_words and len(args.note_words) > 0:
            raw_input = " ".join(args.note_words).strip()
            print(f' -> Parsing direct command-line note: "{raw_input}"...')
            tasks = extract_and_score_notes(raw_input)
        elif args.notes:
            notes_path = Path(args.notes)
            if not notes_path.exists():
                _error_exit(
                    f"File not found: {notes_path}",
                    "Check the file path and try again.",
                )
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
                    _error_exit(
                        "No task input provided.",
                        "Pass tasks as arguments, use --notes, or write to data/daily_notes.md.\n  Or run 'scheduler --interactive' for a guided walkthrough.",
                    )
        print(f" -> Successfully scored {len(tasks)} tasks.")
    except SystemExit:
        raise
    except Exception as e:
        _error_exit(
            "Task scoring failed.",
            "Make sure your Gemini API key is configured correctly.",
            detail=str(e),
        )

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
        now=now,
        daily_start_time=daily_start,
        daily_end_time=daily_end,
        continuous_window=continuous_window,
    )

    # Step 3: Render Diff
    print("\n[Step 3/3] Optimization complete. Reviewing proposed schedule diff:")
    diff_text = format_schedule_diff(plan)
    print("\n" + diff_text)

    # Step 4: Interactive Confirmation
    if args.sync and client is not None and plan.placed_tasks:
        print("\n" + "-" * 80)
        if args.yes:
            confirm = "y"
        else:
            try:
                confirm = input("Would you like to sync these placed tasks to Google Calendar? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"
        if confirm in ("y", "yes"):
            print("Syncing events to Google Calendar...")
            created = scheduler.apply_plan(plan, dry_run=False)
            print(f"Successfully synced {len(created)} events to your calendar!")
        else:
            print("Sync aborted by user. No calendar changes were made.")
    else:
        print("\nSchedule generation complete. Run with --sync to write to Google Calendar.")


if __name__ == "__main__":
    main()
