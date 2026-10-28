"""Chat with Claude over SMS or a phone call.

Twilio forwards each incoming text to POST /sms. We look up that phone
number's recent conversation, ask Claude for a reply, and text it back.

Incoming calls hit POST /voice. Twilio turns the caller's speech into
text, we ask Claude, and Twilio reads the reply aloud, in a loop until
the caller hangs up or says goodbye.

Claude can search the web and, if set up, use the owner's Gmail. That can
take longer than Twilio waits for a webhook (15 seconds), so answers are
worked out in the background: calls wait on /voice/wait, and texts are
sent through Twilio's API once ready.
"""

import os
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
from flask import Flask, abort, request
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

import gmail
from pages import pages

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
# Lets the app text replies once they're ready, so slow answers aren't cut off.
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
# Comma-separated phone numbers allowed to use the bot, e.g. "+15551234567".
# Leave empty to allow anyone who texts your number (they'd spend your credits).
ALLOWED_NUMBERS = {n.strip() for n in os.environ.get("ALLOWED_NUMBERS", "").split(",") if n.strip()}
DB_PATH = os.environ.get("DB_PATH", "conversations.db")
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "20"))
MAX_SMS_CHARS = 1500  # Twilio's limit is 1600 characters per message
VOICE_MODEL = os.environ.get("VOICE_MODEL", "claude-sonnet-5-5")
# Any Twilio text-to-speech voice name, e.g. "Polly.Matthew-Neural".
VOICE_NAME = os.environ.get("VOICE_NAME", "Polly.Joanna-Neural")
# Lets Claude know today's date and time where you live.
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")
# Set WEB_SEARCH=off to stop Claude looking things up online (each search costs about a cent).
WEB_SEARCH = os.environ.get("WEB_SEARCH", "on").lower() not in {"off", "no", "false", "0"}
# Models that support the newer web search tool, which filters results before reading them.
NEW_SEARCH_MODELS = ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                     "claude-sonnet-5", "claude-sonnet-4-6")

SYSTEM_PROMPT = (
    "You are Claude, chatting with someone over text message (SMS). "
    "Keep replies short and conversational, ideally under 300 characters. "
    "Use plain text only: no markdown, bullet symbols, headers, or tables. "
    "For anything current, like news, weather, scores, prices, or hours, search the web first."
)

VOICE_SYSTEM_PROMPT = (
    "You are Claude, talking with someone on a phone call. Your words will be read aloud "
    "by a text-to-speech voice. Answer in one to three short, natural spoken sentences. "
    "Never use markdown, lists, emoji, URLs, or symbols that don't read well aloud. "
    "For anything current, like news, weather, scores, prices, or hours, search the web first."
)
GOODBYES = {"bye", "goodbye", "good bye", "bye bye", "hang up", "that's all", "thats all"}

HELP_TEXT = (
    "Claude SMS Assistant: text anything to chat with Claude. Send RESET to start fresh. "
    "Reply STOP to opt out. Msg & data rates may apply."
)

app = Flask(__name__)
app.register_blueprint(pages)
# Remove all whitespace from the key: a space or line break picked up while pasting it
# makes every request fail with "Connection error". Real keys never contain whitespace.
client = anthropic.Anthropic(api_key="".join(os.environ.get("ANTHROPIC_API_KEY", "").split()), timeout=60)
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


def search_tool(model, max_uses):
    version = "web_search_20260209" if model.startswith(NEW_SEARCH_MODELS) else "web_search_20250305"
    return {"type": version, "name": "web_search", "max_uses": max_uses}


def ask_claude(history, conversation, model=ANTHROPIC_MODEL, system=SYSTEM_PROMPT, max_tokens=1200,
               searches=3):
    request_number = gmail.new_request()
    now = datetime.now(ZoneInfo(TIMEZONE))
    system = f"{system} Right now it is {now:%A, %B %-d, %Y, %-I:%M %p} ({TIMEZONE})."
    tools = [search_tool(model, searches)] if WEB_SEARCH and searches else []
    if gmail.ENABLED:
        tools += gmail.TOOLS
        system += gmail.PROMPT
    extra = {"tools": tools} if tools else {}
    messages = list(history)
    text = []
    for _ in range(10):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            **extra,
        )
        messages.append({"role": "assistant", "content": response.content})
        text += [block.text for block in response.content if block.type == "text"]
        if response.stop_reason == "tool_use":
            # Claude asked to use Gmail: run it and hand back the results.
            # Only the text after the last tool use is the reply.
            text = []
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": block.id,
                 "content": gmail.run_tool(block.name, block.input, conversation, request_number)}
                for block in response.content if block.type == "tool_use"
            ]})
        # A long web search can pause partway; send it back so Claude can finish.
        elif response.stop_reason != "pause_turn":
            break
    return "".join(text).strip() or "(Claude had no reply.)"


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

    if not TWILIO_ACCOUNT_SID:
        return twiml(answer_text(phone, body))
    # Reply in the background and text the answer when it's ready.
    threading.Thread(target=send_text_reply, args=(phone, request.form.get("To", ""), body),
                     daemon=True).start()
    return str(MessagingResponse()), 200, {"Content-Type": "application/xml"}


def answer_text(phone, body):
    history = load_history(phone) + [{"role": "user", "content": body}]
    try:
        reply = ask_claude(history, phone)
    except anthropic.APIError as e:
        app.logger.error("Claude API error: %s (cause: %s)", e, type(e.__cause__).__name__)
        return "Sorry, I couldn't reach Claude just now. Please try again in a minute."
    save(phone, "user", body)
    save(phone, "assistant", reply)
    return reply


def send_text_reply(phone, our_number, body):
    reply = answer_text(phone, body)
    try:
        TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN).messages.create(
            to=phone, from_=our_number, body=reply[:MAX_SMS_CHARS])
    except Exception as e:
        app.logger.error("Couldn't send text: %s", e)


def voice_twiml(resp):
    return str(resp), 200, {"Content-Type": "application/xml"}


def listen(resp, prompt=None):
    """Ask Twilio to record the caller's next sentence and send it to /voice/reply."""
    gather = resp.gather(input="speech", action="/voice/reply", method="POST",
                         speech_timeout="auto", language="en-US")
    if prompt:
        gather.say(prompt, voice=VOICE_NAME)
    # Reached only if the caller says nothing.
    resp.say("I didn't hear anything, so I'll hang up. Call back any time.", voice=VOICE_NAME)
    resp.hangup()


@app.post("/voice")
def voice():
    if not is_from_twilio():
        abort(403)
    resp = VoiceResponse()
    if ALLOWED_NUMBERS and request.form.get("From", "") not in ALLOWED_NUMBERS:
        resp.say("Sorry, this number isn't set up to talk with Claude.", voice=VOICE_NAME)
        resp.hangup()
        return voice_twiml(resp)
    listen(resp, "Hi, this is Claude. What's on your mind?")
    return voice_twiml(resp)


@app.post("/voice/reply")
def voice_reply():
    if not is_from_twilio():
        abort(403)
    resp = VoiceResponse()
    caller = request.form.get("From", "")
    if ALLOWED_NUMBERS and caller not in ALLOWED_NUMBERS:
        resp.hangup()
        return voice_twiml(resp)

    said = request.form.get("SpeechResult", "").strip()
    if not said:
        listen(resp, "Sorry, I didn't catch that. Could you say it again?")
        return voice_twiml(resp)
    if said.lower().strip(" .!?") in GOODBYES:
        resp.say("Talk to you later. Goodbye!", voice=VOICE_NAME)
        resp.hangup()
        return voice_twiml(resp)

    # Each call is its own conversation.
    call = request.form.get("CallSid", caller)
    job = {"done": threading.Event(), "reply": None, "started": time.monotonic()}
    with calls_lock:
        calls[call] = job
    threading.Thread(target=answer_call, args=(call, said, job), daemon=True).start()
    # Most answers are ready in a few seconds; if not, let the caller know and keep waiting.
    if job["done"].wait(6):
        return speak_reply(resp, call)
    resp.say("One moment.", voice=VOICE_NAME)
    resp.redirect("/voice/wait", method="POST")
    return voice_twiml(resp)


calls = {}
calls_lock = threading.Lock()


def answer_call(call, said, job):
    key = "call:" + call
    history = load_history(key) + [{"role": "user", "content": said}]
    try:
        job["reply"] = ask_claude(history, key, model=VOICE_MODEL, system=VOICE_SYSTEM_PROMPT,
                                  max_tokens=800)
        save(key, "user", said)
        save(key, "assistant", job["reply"])
    except anthropic.APIError as e:
        app.logger.error("Claude API error: %s (cause: %s)", e, type(e.__cause__).__name__)
    finally:
        job["done"].set()


def speak_reply(resp, call):
    with calls_lock:
        job = calls.pop(call, None)
    if job and job["reply"]:
        resp.say(job["reply"], voice=VOICE_NAME)
        listen(resp)
    else:
        listen(resp, "Sorry, I couldn't reach Claude just now. Try asking again.")
    return voice_twiml(resp)


@app.post("/voice/wait")
def voice_wait():
    if not is_from_twilio():
        abort(403)
    resp = VoiceResponse()
    call = request.form.get("CallSid", "")
    with calls_lock:
        job = calls.get(call)
    if job is None:
        listen(resp, "Sorry, I lost track of that. Could you ask again?")
        return voice_twiml(resp)
    if job["done"].wait(10) or time.monotonic() - job["started"] > 90:
        return speak_reply(resp, call)
    resp.redirect("/voice/wait", method="POST")
    return voice_twiml(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
