"""Chat with Claude over SMS.

Twilio forwards each incoming text to POST /sms. We look up that phone
number's recent conversation, ask Claude for a reply, and send it back
as TwiML so Twilio texts it to the sender.
"""

import os
import sqlite3
import threading

import anthropic
from flask import Flask, abort, request
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from pages import pages

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
# Comma-separated phone numbers allowed to use the bot, e.g. "+15551234567".
# Leave empty to allow anyone who texts your number (they'd spend your credits).
ALLOWED_NUMBERS = {n.strip() for n in os.environ.get("ALLOWED_NUMBERS", "").split(",") if n.strip()}
DB_PATH = os.environ.get("DB_PATH", "conversations.db")
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "20"))
MAX_SMS_CHARS = 1500  # Twilio's limit is 1600 characters per message

SYSTEM_PROMPT = (
    "You are Claude, chatting with someone over text message (SMS). "
    "Keep replies short and conversational, ideally under 300 characters. "
    "Use plain text only: no markdown, bullet symbols, headers, or tables."
)

HELP_TEXT = (
    "Claude SMS Assistant: text anything to chat with Claude. Send RESET to start fresh. "
    "Reply STOP to opt out. Msg & data rates may apply."
)

app = Flask(__name__)
app.register_blueprint(pages)
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
db_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " phone TEXT NOT NULL,"
        " role TEXT NOT NULL,"
        " content TEXT NOT NULL)"
    )
    return conn


def load_history(phone):
    with db_lock, db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE phone = ? ORDER BY id DESC LIMIT ?",
            (phone, HISTORY_TURNS * 2),
        ).fetchall()
    history = [{"role": r, "content": c} for r, c in reversed(rows)]
    # The API requires the conversation to start with a user message.
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history


def save(phone, role, content):
    with db_lock, db() as conn:
        conn.execute("INSERT INTO messages (phone, role, content) VALUES (?, ?, ?)", (phone, role, content))


def clear_history(phone):
    with db_lock, db() as conn:
        conn.execute("DELETE FROM messages WHERE phone = ?", (phone,))


def ask_claude(history):
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=history,
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text or "(Claude had no reply.)"


def is_from_twilio():
    if not TWILIO_AUTH_TOKEN:
        return True  # validation off, only for local testing
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    # Behind a host's proxy the request may arrive as http even though Twilio called https.
    url = request.url
    if request.headers.get("X-Forwarded-Proto") == "https":
        url = url.replace("http://", "https://", 1)
    return validator.validate(url, request.form, request.headers.get("X-Twilio-Signature", ""))


def twiml(text):
    resp = MessagingResponse()
    resp.message(text[:MAX_SMS_CHARS])
    return str(resp), 200, {"Content-Type": "application/xml"}


@app.post("/sms")
def sms():
    if not is_from_twilio():
        abort(403)

    phone = request.form.get("From", "")
    body = request.form.get("Body", "").strip()

    if ALLOWED_NUMBERS and phone not in ALLOWED_NUMBERS:
        return twiml("Sorry, this number isn't set up to chat.")
    if not body:
        return twiml(HELP_TEXT)
    if body.upper() in {"RESET", "NEW", "CLEAR"}:
        clear_history(phone)
        return twiml("Conversation cleared. What's on your mind?")
    if body.upper() in {"HELP", "?"}:
        return twiml(HELP_TEXT)

    history = load_history(phone) + [{"role": "user", "content": body}]
    try:
        reply = ask_claude(history)
    except anthropic.APIError as e:
        app.logger.error("Claude API error: %s", e)
        return twiml("Sorry, I couldn't reach Claude just now. Please try again in a minute.")

    save(phone, "user", body)
    save(phone, "assistant", reply)
    return twiml(reply)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
