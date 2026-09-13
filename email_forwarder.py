


import os
import sys
import json
import base64
import time
import random
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import unescape
import re

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

ACCOUNT_2_EMAIL = "hemug67@gmail.com"


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
PROCESSED_IDS_FILE = "processed_ids.json"


SEARCH_QUERY = "is:starred newer_than:2d"

# Retry configuration for HTTP 429 rate-limit handling.
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2


# ---------------------------------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------------------------------

def authenticate_gmail():

    creds = None

    if not os.path.exists(CREDENTIALS_FILE):
        print(
            f"ERROR: '{CREDENTIALS_FILE}' not found. Follow the setup steps in the "
            f"header comment (Google Cloud Console -> OAuth client ID -> Desktop app) "
            f"and place the downloaded file here."
        )
        sys.exit(1)

    # Load cached token if present.
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"WARNING: '{TOKEN_FILE}' is invalid or corrupted ({e}). Re-authenticating.")
            creds = None

    # Refresh or run the interactive flow as needed.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"ERROR: Failed to refresh credentials ({e}). Re-running auth flow.")
                creds = None

        if not creds or not creds.valid:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            except FileNotFoundError:
                print(f"ERROR: Missing or invalid '{CREDENTIALS_FILE}'.")
                sys.exit(1)
            except Exception as e:
                print(f"ERROR: OAuth2 authentication failed: {e}")
                sys.exit(1)

        # Cache the token for next run.
        try:
            with open(TOKEN_FILE, "w") as token_file:
                token_file.write(creds.to_json())
        except OSError as e:
            print(f"WARNING: Could not write '{TOKEN_FILE}' ({e}). Will need to re-auth next run.")

    return creds


# ---------------------------------------------------------------------------
# DEDUPE TRACKING (processed_ids.json)
# ---------------------------------------------------------------------------

def load_processed_ids():
    """Loads the set of already-processed Gmail message IDs from disk."""
    if not os.path.exists(PROCESSED_IDS_FILE):
        return set()
    try:
        with open(PROCESSED_IDS_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
            return set()
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: Could not read '{PROCESSED_IDS_FILE}' ({e}). Starting with empty set.")
        return set()


def save_processed_ids(processed_ids):
    """Persists the set of processed message IDs back to disk."""
    try:
        with open(PROCESSED_IDS_FILE, "w") as f:
            json.dump(sorted(processed_ids), f, indent=2)
    except OSError as e:
        print(f"WARNING: Could not write '{PROCESSED_IDS_FILE}' ({e}). Dedupe state not saved.")


# ---------------------------------------------------------------------------
# RETRY WRAPPER FOR HTTP 429 HANDLING
# ---------------------------------------------------------------------------

def execute_with_retry(request, description="API call"):
    """
    Executes a Gmail API request object, retrying with exponential backoff
    on HTTP 429 (rate limit) errors, per Assumption E above.
    Raises the underlying error after MAX_RETRIES is exhausted, or immediately
    for non-429 errors (caller handles those).
    """
    attempt = 0
    while True:
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status == 429 and attempt < MAX_RETRIES:
                attempt += 1
                backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1)
                print(
                    f"WARNING: HTTP 429 rate limit hit during {description}. "
                    f"Retrying in {backoff:.1f}s (attempt {attempt}/{MAX_RETRIES})..."
                )
                time.sleep(backoff)
                continue
            # Either not a 429, or retries exhausted -- re-raise for caller to handle.
            raise


# ---------------------------------------------------------------------------
# SEARCH: find starred messages from the last 2 days (server-side query)
# ---------------------------------------------------------------------------

def search_starred_recent_messages(service):

    message_ids = []
    page_token = None

    while True:
        request = service.users().messages().list(
            userId="me",
            q=SEARCH_QUERY,
            pageToken=page_token,
        )
        try:
            response = execute_with_retry(request, description="messages.list search")
        except HttpError as e:
            print(f"ERROR: Failed to search mailbox: {e}")
            return message_ids  # Return whatever we've collected so far (possibly empty).

        for msg in response.get("messages", []):
            message_ids.append(msg["id"])

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return message_ids


# ---------------------------------------------------------------------------
# DECODE: extract sender, subject, date, and body from a full message
# ---------------------------------------------------------------------------

def _get_header(headers, name):
    """Helper: case-insensitive header lookup from a Gmail API headers list."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _strip_html_tags(html_text):
    """
    Very lightweight HTML-to-text fallback used only when no text/plain part
    exists (see Assumption C). Not a full HTML renderer -- just strips tags
    and unescapes entities so the quoted body is readable.
    """
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def _decode_base64url(data):
    """Decodes Gmail API's base64url-encoded body data to a UTF-8 string."""
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (base64.binascii.Error, UnicodeDecodeError) as e:
        raise ValueError(f"Failed to decode message body: {e}")


def _extract_body_from_parts(parts):
    """
    Recursively walks a MIME parts tree to find text/plain (preferred) or
    text/html (fallback) content, per Assumption C.
    Returns (plain_text_or_None, html_text_or_None).
    """
    plain_text = None
    html_text = None

    for part in parts:
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")

        if mime_type == "text/plain" and data and plain_text is None:
            plain_text = _decode_base64url(data)
        elif mime_type == "text/html" and data and html_text is None:
            html_text = _decode_base64url(data)
        elif mime_type.startswith("multipart/") and "parts" in part:
            sub_plain, sub_html = _extract_body_from_parts(part["parts"])
            if plain_text is None:
                plain_text = sub_plain
            if html_text is None:
                html_text = sub_html

    return plain_text, html_text


def decode_message_body(payload):
    """
    Decodes the body of a Gmail message payload, handling both simple
    (non-multipart) and multipart MIME structures.

    Returns the best available text body as a string, or raises ValueError
    if no readable body could be extracted (caller handles this per the
    "messages with no readable body" error-handling constraint).
    """
    mime_type = payload.get("mimeType", "")

    # Simple, non-multipart message.
    if not mime_type.startswith("multipart/"):
        data = payload.get("body", {}).get("data")
        if not data:
            raise ValueError("No body data found in non-multipart message.")
        text = _decode_base64url(data)
        if mime_type == "text/html":
            text = _strip_html_tags(text)
        if not text.strip():
            raise ValueError("Decoded body is empty.")
        return text

    # Multipart message: walk the tree for text/plain or text/html.
    parts = payload.get("parts", [])
    if not parts:
        raise ValueError("Multipart message has no parts.")

    plain_text, html_text = _extract_body_from_parts(parts)

    if plain_text and plain_text.strip():
        return plain_text
    if html_text and html_text.strip():
        return _strip_html_tags(html_text)

    raise ValueError("No text/plain or text/html content found in message parts.")


def fetch_and_decode_message(service, message_id):
    """
    Fetches a full message by ID and extracts sender, subject, date, and body.
    Raises ValueError (caller catches) if the body cannot be read, so the
    overall run continues to the next message instead of crashing.
    """
    request = service.users().messages().get(
        userId="me", id=message_id, format="full"
    )
    msg = execute_with_retry(request, description=f"messages.get({message_id})")

    payload = msg.get("payload", {})
    headers = payload.get("headers", [])

    sender = _get_header(headers, "From") or "(unknown sender)"
    subject = _get_header(headers, "Subject") or "(no subject)"
    date_str = _get_header(headers, "Date") or "(unknown date)"

    body = decode_message_body(payload)  # May raise ValueError -- caller handles.

    return {
        "id": message_id,
        "sender": sender,
        "subject": subject,
        "date": date_str,
        "body": body,
    }


# ---------------------------------------------------------------------------
# COMPOSE: build the outgoing "manual forward" email
# ---------------------------------------------------------------------------

def compose_forward_message(original, from_address, to_address):
    """
    Builds a MIMEText message that quotes the original email's sender,
    subject, date, and full body -- functioning as a manual forward rather
    than Gmail's native Forward (which would preserve original MIME
    structure/threading; this constructs an entirely new message instead).

    Returns a base64url-encoded raw message dict ready for users.messages.send.
    """
    quoted_body = (
        f"---------- Forwarded message ----------\n"
        f"From: {original['sender']}\n"
        f"Date: {original['date']}\n"
        f"Subject: {original['subject']}\n\n"
        f"{original['body']}\n"
    )

    new_subject = f"Fwd: {original['subject']}"

    message = MIMEText(quoted_body, "plain", "utf-8")
    message["To"] = to_address
    message["From"] = from_address
    message["Subject"] = new_subject

    raw_bytes = message.as_bytes()
    raw_base64url = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

    return {"raw": raw_base64url}


# ---------------------------------------------------------------------------
# SEND
# ---------------------------------------------------------------------------

def send_message(service, raw_message):
    """
    Sends a pre-composed, base64url-encoded raw message via users.messages.send.
    Wrapped in the retry helper to handle transient 429s.
    """
    request = service.users().messages().send(userId="me", body=raw_message)
    return execute_with_retry(request, description="messages.send")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print(f"Starting Gmail starred-forwarder run at {datetime.now().isoformat()}")

    if ACCOUNT_2_EMAIL == "ACCOUNT_2_PLACEHOLDER@example.com":
        print(
            "WARNING: ACCOUNT_2_EMAIL is still set to its placeholder value. "
            "Edit the constant near the top of this script before relying on real sends."
        )

    # --- Authenticate ---
    try:
        creds = authenticate_gmail()
    except Exception as e:
        print(f"ERROR: Authentication failed unexpectedly: {e}")
        sys.exit(1)

    try:
        service = build("gmail", "v1", credentials=creds)
    except Exception as e:
        print(f"ERROR: Could not build Gmail API service client: {e}")
        sys.exit(1)

    # Determine ACCOUNT_1's own address for the "From" header, via the
    # authenticated profile rather than assuming/hardcoding it.
    try:
        profile_request = service.users().getProfile(userId="me")
        profile = execute_with_retry(profile_request, description="getProfile")
        account_1_email = profile.get("emailAddress", "me")
    except HttpError as e:
        print(f"ERROR: Could not fetch ACCOUNT_1 profile (needed for 'From' header): {e}")
        sys.exit(1)

    print(f"Authenticated as ACCOUNT_1: {account_1_email}")

    # --- Dedupe state ---
    processed_ids = load_processed_ids()

    # --- Search (server-side query, per constraint) ---
    print(f"Searching with query: '{SEARCH_QUERY}'")
    message_ids = search_starred_recent_messages(service)
    print(f"Found {len(message_ids)} matching message(s).")

    new_ids_to_process = [mid for mid in message_ids if mid not in processed_ids]
    skipped_count = len(message_ids) - len(new_ids_to_process)
    if skipped_count > 0:
        print(f"Skipping {skipped_count} already-processed message(s) (dedupe).")

    sent_count = 0
    error_count = 0

    for message_id in new_ids_to_process:
        try:
            original = fetch_and_decode_message(service, message_id)
        except ValueError as e:
            # "No readable body" or decode failure -- log and continue, don't crash.
            print(f"ERROR: Skipping message {message_id} (no readable body): {e}")
            error_count += 1
            continue
        except HttpError as e:
            status = getattr(e.resp, "status", "unknown")
            print(f"ERROR: Skipping message {message_id} (HTTP {status} during fetch): {e}")
            error_count += 1
            continue
        except Exception as e:
            print(f"ERROR: Skipping message {message_id} (unexpected error during fetch): {e}")
            error_count += 1
            continue

        try:
            raw_message = compose_forward_message(
                original, from_address=account_1_email, to_address=ACCOUNT_2_EMAIL
            )
        except Exception as e:
            print(f"ERROR: Failed to compose forward for message {message_id}: {e}")
            error_count += 1
            continue

        try:
            send_message(service, raw_message)
        except HttpError as e:
            status = getattr(e.resp, "status", "unknown")
            print(
                f"ERROR: Failed to send forward for '{original['subject']}' "
                f"(HTTP {status}): {e}"
            )
            error_count += 1
            continue
        except Exception as e:
            print(f"ERROR: Failed to send forward for '{original['subject']}': {e}")
            error_count += 1
            continue

        # Success -- record dedupe state immediately so a crash mid-run
        # doesn't cause a re-send of already-sent messages on next run.
        processed_ids.add(message_id)
        save_processed_ids(processed_ids)

        print(f"Sent: {original['subject']}")
        sent_count += 1

    print(
        f"Run complete. Sent {sent_count} email(s), "
        f"{error_count} error(s), {skipped_count} skipped as duplicates."
    )


if __name__ == "__main__":
    main()
