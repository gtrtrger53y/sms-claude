"""Chat with Claude over SMS or a phone call, with one shared number for many people.

Every phone number that texts in is its own little account, saved in the
database (Postgres such as Neon, or SQLite for testing): its own conversation,
its own memory of things it asked Claude to remember, and optionally its own
connected Gmail. It's private: only numbers the owner has added (friends) reach
Claude. Anyone else gets one short reply saying it's private.

The owner manages members by texting the service number:
  ADD +15551234567 [name]   let a number in (also un-bans)
  REMOVE +15551234567       take a number out
  BAN +15551234567          block a number completely: no replies, calls rejected
  LIST                      see who's in
  WAITING                   see numbers that texted but aren't in yet

On a call, the owner can press 8 five times (88888) while Claude is listening to
open a keypad admin menu: add, ban, or hear the member and waiting lists.

Twilio forwards each incoming text to POST /sms and each call to POST /voice.
Answers that take a while (web search, email) are worked out in the background:
calls wait on /voice/wait, and texts are sent through Twilio's API once ready.
"""

import os
import re
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
import store
from pages import PAY_LINK, PRICE_TEXT, SERVICE_NAME, pages

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
# Lets the app text replies once they're ready, so slow answers aren't cut off,
# and lets it text the owner when a new number writes in.
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
# Numbers that are always allowed, comma-separated, e.g. "+15551234567". More can be
# added by texting ADD. The owner's number (below) is always allowed.
ALLOWED_NUMBERS = {p for p in (store.normalize_phone(n) for n in
                               os.environ.get("ALLOWED_NUMBERS", "").split(",")) if p}
# The number(s) that can text ADD / REMOVE / LIST and open the call admin menu, comma-separated
# if you have more than one phone. Defaults to the first ALLOWED_NUMBERS entry.
OWNER_NUMBERS = [p for p in (store.normalize_phone(n) for n in
                             os.environ.get("OWNER_NUMBER", "").split(",")) if p] or [
    p for p in (store.normalize_phone(n) for n in os.environ.get("ALLOWED_NUMBERS", "").split(",")) if p][:1]
# The first owner number gets the "new number texted" notices.
OWNER_NUMBER = OWNER_NUMBERS[0] if OWNER_NUMBERS else None
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "20"))
# Most texts a member (not the owner) can send in 24 hours. 0 means no limit.
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "50"))
MAX_SMS_CHARS = 1500  # Twilio's limit is 1600 characters per message
MAX_MEMORY_CHARS = 2000
VOICE_MODEL = os.environ.get("VOICE_MODEL", "claude-sonnet-5-5")
# Any Twilio text-to-speech voice name, e.g. "Polly.Matthew-Neural".
VOICE_NAME = os.environ.get("VOICE_NAME", "Polly.Joanna-Neural")
# How fast the voice talks: "100%" is normal, "120%" a bit quicker. Works with Polly voices.
VOICE_RATE = os.environ.get("VOICE_RATE", "120%").strip()
# Keys the owner presses during a call to open the admin menu.
ADMIN_CODE = os.environ.get("ADMIN_CODE", "88888").strip()
# Speech recognition model for calls. Other options: deepgram_nova-3, phone_call, googlev2_chirp_3.
SPEECH_MODEL = os.environ.get("SPEECH_MODEL", "googlev2_telephony").strip()
# Seconds of silence before your turn ends, or "auto" for the first pause.
SPEECH_TIMEOUT = os.environ.get("SPEECH_TIMEOUT", "auto").strip() or "auto"
# Lets Claude know today's date and time where you live.
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")
# Set WEB_SEARCH=off to stop Claude looking things up online (each search costs about a cent).
WEB_SEARCH = os.environ.get("WEB_SEARCH", "on").lower() not in {"off", "no", "false", "0"}
# The app's public address, for email-connect links. Render fills in RENDER_EXTERNAL_URL itself.
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
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

MEMORY_PROMPT = (
    " You have a memory for this person that lasts between conversations. When they tell you "
    "something lasting about themselves (their name, where they live, preferences, people in "
    "their life) or ask you to remember something, save it with the remember tool, as one short "
    "note. If they ask you to forget something, use the forget tool. Don't announce that you "
    "saved a note unless they asked you to remember it."
)

MEMORY_TOOLS = [
    {
        "name": "remember",
        "description": "Save one short, lasting note about this person for future conversations.",
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string", "description": "One short fact, e.g. 'Lives in Denver'"}},
            "required": ["note"],
        },
    },
    {
        "name": "forget",
        "description": "Delete saved notes that contain this text.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Words from the note to delete"}},
            "required": ["text"],
        },
    },
]

GOODBYES = {"bye", "goodbye", "good bye", "bye bye", "hang up", "that's all", "thats all"}
# Twilio handles these itself (opt-out and opt-back-in), so the app stays quiet.
OPT_OUT_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "REVOKE",
                 "START", "UNSTOP"}

HELP_TEXT = (
    f"{SERVICE_NAME}: text anything to chat with Claude, an AI. RESET starts fresh. MEMORY shows what "
    "I remember, FORGET ALL clears it. CONNECT EMAIL links your Gmail. DELETE MY DATA erases "
    "everything. Reply STOP to opt out. Msg & data rates may apply."
)
OWNER_HELP = ("Owner: ADD +15551234567 [name], REMOVE +15551234567, BAN +15551234567, LIST, WAITING. "
              f"On a call, press {ADMIN_CODE} for the keypad admin menu.")

app = Flask(__name__)
app.register_blueprint(pages)
# Remove all whitespace from the key: a space or line break picked up while pasting it
# makes every request fail with "Connection error". Real keys never contain whitespace.
client = anthropic.Anthropic(api_key="".join(os.environ.get("ANTHROPIC_API_KEY", "").split()), timeout=60)

_db_ready = False
_db_lock = threading.Lock()


def ensure_db():
    global _db_ready
    if not _db_ready:
        with _db_lock:
            if not _db_ready:
                store.init()
                _db_ready = True


try:
    ensure_db()
except Exception as e:  # The database may still be waking up; try again on the first text.
    app.logger.error("Database not ready at startup: %s", e)


@app.before_request
def before():
    ensure_db()


# ---------- small rate limits (in memory, reset when the app restarts) ----------

_counters = {}
_counters_lock = threading.Lock()


def allow(kind, per_day):
    """True at most per_day times a day for this kind of message, so spam can't run up the bill."""
    day = int(time.time() // 86400)
    with _counters_lock:
        key = (kind, day)
        _counters[key] = _counters.get(key, 0) + 1
        for old in [k for k in _counters if k[1] != day]:
            del _counters[old]
        return _counters[key] <= per_day


# ---------- accounts ----------

def account(phone):
    """Return (role, user) where role is 'owner', 'member', or None for everyone else."""
    if not phone:
        return None, None
    if phone in OWNER_NUMBERS:
        return "owner", store.ensure_user(phone, "active")
    user = store.get_user(phone)
    if phone in ALLOWED_NUMBERS:
        if not user or user["status"] == "pending":
            store.set_status(phone, "active")
            user = store.get_user(phone)
        if user["status"] == "active":
            return "member", user
    if user and user["status"] == "active":
        return "member", user
    return None, user


def send_text(to, from_, body):
    if not TWILIO_ACCOUNT_SID:
        return False
    try:
        TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN).messages.create(
            to=to, from_=from_, body=body[:MAX_SMS_CHARS])
        return True
    except Exception as e:
        app.logger.error("Couldn't send text: %s", e)
        return False


def stranger_reply(phone, body, our_number, user):
    """A number that isn't a member texted. Reply once a day at most, and tell the owner once."""
    new = user is None
    if new:
        user = store.ensure_user(phone, "pending")
        if OWNER_NUMBER and allow("owner-notice", 20):
            send_text(OWNER_NUMBER, our_number,
                      f"New number {phone} texted {SERVICE_NAME}: \"{body[:80]}\". "
                      f"Reply ADD {phone} to let them in.")
    store.touch(phone)
    if time.time() - (user.get("last_notice") or 0) < 86400 or not allow("stranger-reply", 200):
        return ""
    store.set_last_notice(phone)
    text = f"Hi! {SERVICE_NAME} is a private AI assistant by text, just for friends of its owner."
    if PAY_LINK:
        text += f" If you're joining{' (' + PRICE_TEXT + ')' if PRICE_TEXT else ''}, pay here: {PAY_LINK} "
        text += "You'll get a text once your number is added."
    else:
        text += " If you know the owner, ask them to add your number."
    return text + " Reply STOP to opt out."


def owner_command(body, our_number):
    """Handle ADD / REMOVE / LIST from the owner. Returns a reply, or None if it isn't a command."""
    words = body.split()
    command = words[0].upper() if words else ""
    if command == "LIST" and len(words) == 1:
        members = store.list_users("active")
        others = [(p, n) for p, n in members if p not in OWNER_NUMBERS]
        if not others:
            return "No members yet besides you. Text ADD +15551234567 to add one."
        lines = [f"{p}{' ' + n if n else ''}" for p, n in others]
        return f"{len(others)} member(s):\n" + "\n".join(lines)
    if command == "WAITING" and len(words) == 1:
        waiting = store.list_users("pending")[-15:]
        if not waiting:
            return "Nobody is waiting."
        return "Texted but not added yet:\n" + "\n".join(p for p, _ in waiting) + "\nReply ADD +1... to add one."
    if command not in {"ADD", "REMOVE", "BAN"} or len(words) < 2:
        return None
    # The number may be typed with spaces, like "ADD +1 555 123 4567 Sam".
    end = 1
    while end < len(words) and re.fullmatch(r"[\d+().\-]+", words[end]):
        end += 1
    phone, rest = store.normalize_phone(" ".join(words[1:end])), words[end:]
    if not phone:
        return f"I couldn't read that number. Try: {command} +15551234567"
    if phone in OWNER_NUMBERS:
        return "That's your own number; it's always allowed."
    name = " ".join(rest)[:40]
    if command == "ADD":
        welcomed = add_member(phone, name, our_number)
        return f"Added {phone}{' (' + name + ')' if name else ''}." + (" I texted them." if welcomed else "")
    if command == "BAN":
        ban_number(phone)
        return f"Banned {phone}. They get no replies and their calls are rejected. ADD them to undo."
    user = store.get_user(phone)
    if not user or user["status"] != "active":
        return f"{phone} isn't a member."
    store.set_status(phone, "removed")
    return f"Removed {phone}. Their saved chats stay until they text DELETE MY DATA."


def add_member(phone, name, our_number):
    """Let a number in (or back in after a ban). Returns True if they were texted a welcome."""
    before = store.get_user(phone)
    store.set_status(phone, "active", name or (before or {}).get("name", ""))
    # Only text people who already texted in (they asked to join).
    if before and before.get("last_seen") and before.get("status") != "banned":
        return send_text(phone, our_number,
                         f"You're in! Text anything to chat with {SERVICE_NAME}. Text HELP for "
                         "commands. Reply STOP to opt out.")
    return False


def ban_number(phone):
    """Block a number completely: its texts get no reply and its calls are rejected."""
    store.set_status(phone, "banned")


def connect_link(phone):
    if not gmail.can_store_passwords():
        return "Email connecting isn't set up yet (the owner needs to add SECRET_KEY)."
    base = PUBLIC_URL or request.url_root.rstrip("/").replace("http://", "https://", 1)
    token = store.new_link_token(phone, "gmail")
    return (f"Open this link to connect your Gmail (works once, for 15 minutes): {base}/connect/{token} "
            "Never text your password here.")


# ---------- Claude ----------

def search_tool(model, max_uses):
    version = "web_search_20260209" if model.startswith(NEW_SEARCH_MODELS) else "web_search_20250305"
    return {"type": version, "name": "web_search", "max_uses": max_uses}


def remember(phone, note):
    note = " ".join(str(note).split())[:200]
    if not note:
        return "Nothing to save."
    memory = (store.get_user(phone) or {}).get("memory", "")
    notes = [n for n in memory.split("\n") if n]
    if note in notes:
        return "Already saved."
    notes.append(note)
    while len("\n".join(notes)) > MAX_MEMORY_CHARS and len(notes) > 1:
        notes.pop(0)  # oldest notes go first when memory is full
    store.set_memory(phone, "\n".join(notes))
    return "Saved."


def forget(phone, text):
    text = str(text).strip().lower()
    memory = (store.get_user(phone) or {}).get("memory", "")
    notes = [n for n in memory.split("\n") if n]
    kept = [n for n in notes if text not in n.lower()] if text else notes
    store.set_memory(phone, "\n".join(kept))
    return f"Deleted {len(notes) - len(kept)} note(s)."


def run_tool(phone, creds, name, args, request_number):
    if name == "remember":
        return remember(phone, args.get("note", ""))
    if name == "forget":
        return forget(phone, args.get("text", ""))
    if creds:
        return gmail.run_tool(creds, name, args, phone, request_number)
    return f"Unknown tool {name}."


def ask_claude(phone, user, role, history, model=ANTHROPIC_MODEL, system=SYSTEM_PROMPT,
               max_tokens=1200, searches=3):
    request_number = gmail.new_request()
    now = datetime.now(ZoneInfo(TIMEZONE))
    system = f"{system} Right now it is {now:%A, %B %-d, %Y, %-I:%M %p} ({TIMEZONE})."
    if user.get("name"):
        system += f" This person's name is {user['name']}."
    system += MEMORY_PROMPT
    memory = user.get("memory", "")
    system += (" Your saved notes about this person:\n" + memory) if memory else " You have no saved notes yet."
    tools = [search_tool(model, searches)] if WEB_SEARCH and searches else []
    tools += MEMORY_TOOLS
    creds = gmail.credentials_for(user, role == "owner")
    if creds:
        tools += gmail.TOOLS
        system += gmail.PROMPT
    messages = list(history)
    text = []
    for _ in range(10):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )
        messages.append({"role": "assistant", "content": response.content})
        text += [block.text for block in response.content if block.type == "text"]
        if response.stop_reason == "tool_use":
            # Claude asked to use memory or Gmail: run it and hand back the results.
            # Only the text after the last tool use is the reply.
            text = []
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": block.id,
                 "content": run_tool(phone, creds, block.name, block.input, request_number)}
                for block in response.content if block.type == "tool_use"
            ]})
        # A long web search can pause partway; send it back so Claude can finish.
        elif response.stop_reason != "pause_turn":
            break
    return "".join(text).strip() or "(Claude had no reply.)"


# ---------- texts ----------

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
    if text:
        resp.message(text[:MAX_SMS_CHARS])
    return str(resp), 200, {"Content-Type": "application/xml"}


@app.post("/sms")
def sms():
    if not is_from_twilio():
        abort(403)

    phone = store.normalize_phone(request.form.get("From", "")) or request.form.get("From", "")
    our_number = request.form.get("To", "")
    body = request.form.get("Body", "").strip()
    upper = " ".join(body.upper().split())

    if upper in OPT_OUT_WORDS:
        return twiml("")
    role, user = account(phone)
    if role is None and user and user["status"] == "banned":
        return twiml("")
    if role is None:
        return twiml(stranger_reply(phone, body, our_number, user))
    store.touch(phone)

    if not body:
        return twiml(HELP_TEXT)
    if role == "owner":
        reply = owner_command(body, our_number)
        if reply:
            return twiml(reply)
    if upper in {"RESET", "NEW", "CLEAR"}:
        store.clear_history(phone)
        return twiml("Conversation cleared. What's on your mind?")
    if upper in {"HELP", "?"}:
        return twiml(HELP_TEXT + (" " + OWNER_HELP if role == "owner" else ""))
    if upper in {"MEMORY", "MY MEMORY", "WHAT DO YOU REMEMBER"}:
        memory = user.get("memory", "")
        return twiml("What I remember:\n" + memory if memory else
                     "I haven't saved anything about you yet. Tell me something to remember!")
    if upper == "FORGET ALL":
        store.set_memory(phone, "")
        return twiml("Done. I've forgotten everything I had saved about you.")
    if upper in {"CONNECT EMAIL", "CONNECT GMAIL"}:
        return twiml(connect_link(phone))
    if upper in {"DISCONNECT EMAIL", "DISCONNECT GMAIL"}:
        store.set_gmail(phone, "", "")
        extra = " (Your owner email in Render settings still works.)" if role == "owner" and \
            gmail.OWNER_GMAIL_ADDRESS else ""
        return twiml("Email disconnected. You can also delete the app password at "
                     "myaccount.google.com/apppasswords." + extra)
    if upper == "DELETE MY DATA":
        store.delete_user_data(phone)
        return twiml("Deleted your chats, saved notes and email connection.")

    if role != "owner" and DAILY_LIMIT and \
            store.messages_since(phone, time.time() - 86400) >= DAILY_LIMIT:
        return twiml(f"You've reached today's limit of {DAILY_LIMIT} messages. Try again tomorrow!")

    if not TWILIO_ACCOUNT_SID:
        return twiml(answer_text(phone, user, role, body))
    # Reply in the background and text the answer when it's ready.
    threading.Thread(target=send_text_reply, args=(phone, user, role, our_number, body),
                     daemon=True).start()
    return twiml("")


def answer_text(phone, user, role, body):
    history = store.load_history(phone, HISTORY_TURNS) + [{"role": "user", "content": body}]
    try:
        reply = ask_claude(phone, user, role, history)
    except anthropic.APIError as e:
        app.logger.error("Claude API error: %s (cause: %s)", e, type(e.__cause__).__name__)
        return "Sorry, I couldn't reach Claude just now. Please try again in a minute."
    store.save_message(phone, "user", body)
    store.save_message(phone, "assistant", reply)
    store.prune_history(phone, max(HISTORY_TURNS * 2, DAILY_LIMIT * 2) + 10)
    return reply


def send_text_reply(phone, user, role, our_number, body):
    try:
        reply = answer_text(phone, user, role, body)
    except Exception as e:
        app.logger.exception("Couldn't answer: %s", e)
        reply = "Sorry, something went wrong. Please try again."
    send_text(phone, our_number, reply)


# ---------- phone calls ----------

def voice_twiml(resp):
    return str(resp), 200, {"Content-Type": "application/xml"}


def say(target, text):
    """Speak text on a call (or inside a gather), at the VOICE_RATE speed."""
    if VOICE_RATE and VOICE_RATE != "100%":
        target.say(voice=VOICE_NAME).prosody(text, rate=VOICE_RATE)
    else:
        target.say(text, voice=VOICE_NAME)


def listen(resp, prompt=None):
    """Ask Twilio to record the caller's next sentence (or keypad presses) and send it to /voice/reply."""
    options = dict(input="speech dtmf", action="/voice/reply", method="POST",
                   speech_timeout=SPEECH_TIMEOUT, language="en-US",
                   # Keypad: stop as soon as the admin code's worth of keys is pressed.
                   num_digits=len(ADMIN_CODE) or 5, finish_on_key="#")
    if SPEECH_MODEL:
        options["speech_model"] = SPEECH_MODEL
    gather = resp.gather(**options)
    if prompt:
        say(gather, prompt)
    # Reached only if the caller says nothing.
    say(resp, "I didn't hear anything, so I'll hang up. Call back any time.")
    resp.hangup()


def caller_number():
    raw = request.form.get("From", "")
    return store.normalize_phone(raw) or raw


@app.post("/voice")
def voice():
    if not is_from_twilio():
        abort(403)
    resp = VoiceResponse()
    role, user = account(caller_number())
    if role is None and user and user["status"] == "banned":
        resp.reject()  # sounds busy, and the call isn't answered
        return voice_twiml(resp)
    if role is None:
        say(resp, f"Sorry, {SERVICE_NAME} is a private service. If you know the owner, ask them to add your number.")
        resp.hangup()
        return voice_twiml(resp)
    listen(resp, "Hi, this is Claude. What's on your mind?")
    return voice_twiml(resp)


@app.post("/voice/reply")
def voice_reply():
    if not is_from_twilio():
        abort(403)
    resp = VoiceResponse()
    caller = caller_number()
    role, user = account(caller)
    if role is None:
        resp.hangup()
        return voice_twiml(resp)

    digits = request.form.get("Digits", "").strip()
    if digits and ADMIN_CODE and digits == ADMIN_CODE and role == "owner":
        resp.redirect("/voice/admin", method="POST")
        return voice_twiml(resp)
    said = request.form.get("SpeechResult", "").strip()
    if not said:
        listen(resp, "Sorry, I didn't catch that. Could you say it again?")
        return voice_twiml(resp)
    if said.lower().strip(" .!?") in GOODBYES:
        say(resp, "Talk to you later. Goodbye!")
        resp.hangup()
        return voice_twiml(resp)

    # Each call is its own conversation, but shares the caller's memory and email.
    call = request.form.get("CallSid", caller)
    job = {"done": threading.Event(), "reply": None, "started": time.monotonic()}
    with calls_lock:
        calls[call] = job
    threading.Thread(target=answer_call, args=(caller, user, role, call, said, job), daemon=True).start()
    # Most answers are ready in a few seconds; if not, let the caller know and keep waiting.
    if job["done"].wait(6):
        return speak_reply(resp, call)
    say(resp, "One moment.")
    resp.redirect("/voice/wait", method="POST")
    return voice_twiml(resp)


calls = {}
calls_lock = threading.Lock()


def answer_call(caller, user, role, call, said, job):
    key = f"call:{caller}:{call}"
    history = store.load_history(key, HISTORY_TURNS) + [{"role": "user", "content": said}]
    try:
        job["reply"] = ask_claude(caller, user, role, history, model=VOICE_MODEL,
                                  system=VOICE_SYSTEM_PROMPT, max_tokens=800)
        store.save_message(key, "user", said)
        store.save_message(key, "assistant", job["reply"])
    except anthropic.APIError as e:
        app.logger.error("Claude API error: %s (cause: %s)", e, type(e.__cause__).__name__)
    except Exception as e:
        app.logger.exception("Call answer failed: %s", e)
    finally:
        job["done"].set()


def speak_reply(resp, call):
    with calls_lock:
        job = calls.pop(call, None)
    if job and job["reply"]:
        say(resp, job["reply"])
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


# ---------- keypad admin menu (owner only, on a call) ----------

ADMIN_MENU = ("Admin mode. Press 1 to add a number. Press 2 to ban a number. "
              "Press 3 to hear your members. Press 4 to hear numbers waiting to join. "
              "Press star to go back to Claude.")


def admin_caller():
    """The caller's number if they're the owner, else None. Every admin step checks this again."""
    if not is_from_twilio():
        abort(403)
    caller = caller_number()
    role, _ = account(caller)
    return caller if role == "owner" else None


def spoken_number(phone):
    """'+15551234567' -> '5 5 5, 1 2 3, 4 5 6 7', so the voice reads it digit by digit."""
    digits = phone[2:] if phone.startswith("+1") and len(phone) == 12 else phone.lstrip("+")
    groups = [digits[:3], digits[3:6], digits[6:]] if len(digits) == 10 else [digits]
    return ", ".join(" ".join(g) for g in groups if g)


def admin_menu(resp, before=None):
    gather = resp.gather(input="dtmf", num_digits=1, timeout=8,
                         action="/voice/admin/choice", method="POST")
    if before:
        say(gather, before)
    say(gather, ADMIN_MENU)
    # No key pressed: leave admin mode and go back to chatting.
    say(resp, "Leaving admin mode.")
    listen(resp, "What's on your mind?")
    return voice_twiml(resp)


def ask_for_number(resp, action, before=None):
    gather = resp.gather(input="dtmf", finish_on_key="#", timeout=10, num_digits=15,
                         action=f"/voice/admin/number?action={action}", method="POST")
    verb = "add" if action == "add" else "ban"
    say(gather, (before + " " if before else "") +
        f"Enter the phone number to {verb}, then press pound.")
    return admin_menu(resp, "I didn't get a number.")


@app.post("/voice/admin")
def voice_admin():
    resp = VoiceResponse()
    if not admin_caller():
        listen(resp)
        return voice_twiml(resp)
    return admin_menu(resp)


@app.post("/voice/admin/choice")
def voice_admin_choice():
    resp = VoiceResponse()
    if not admin_caller():
        listen(resp)
        return voice_twiml(resp)
    choice = request.form.get("Digits", "")
    if choice == "1":
        return ask_for_number(resp, "add")
    if choice == "2":
        return ask_for_number(resp, "ban")
    if choice == "3":
        members = [(p, n) for p, n in store.list_users("active") if p not in OWNER_NUMBERS]
        if not members:
            return admin_menu(resp, "You have no members yet besides you.")
        names = [n or spoken_number(p) for p, n in members[:10]]
        more = f" and {len(members) - 10} more" if len(members) > 10 else ""
        return admin_menu(resp, f"You have {len(members)} member{'s' if len(members) != 1 else ''}: "
                                + ". ".join(names) + more + ".")
    if choice == "4":
        waiting = store.list_users("pending")[-5:]
        if not waiting:
            return admin_menu(resp, "Nobody is waiting.")
        return admin_menu(resp, "Waiting to join: " + ". ".join(spoken_number(p) for p, _ in waiting) + ".")
    if choice == "*":
        listen(resp, "Okay, back to chatting. What's on your mind?")
        return voice_twiml(resp)
    return admin_menu(resp, "That's not an option.")


@app.post("/voice/admin/number")
def voice_admin_number():
    resp = VoiceResponse()
    if not admin_caller():
        listen(resp)
        return voice_twiml(resp)
    action = "ban" if request.args.get("action") == "ban" else "add"
    phone = store.normalize_phone(request.form.get("Digits", ""))
    if not phone:
        return ask_for_number(resp, action, "I couldn't read that number.")
    if phone in OWNER_NUMBERS:
        return admin_menu(resp, "That's your own number, so I left it alone.")
    gather = resp.gather(input="dtmf", num_digits=1, timeout=8, method="POST",
                         action=f"/voice/admin/confirm?action={action}&phone={phone[1:]}")
    say(gather, f"You entered {spoken_number(phone)}. Press 1 to {action} it. "
                "Press 2 to enter it again. Press star to cancel.")
    return admin_menu(resp, "Nothing changed.")


@app.post("/voice/admin/confirm")
def voice_admin_confirm():
    resp = VoiceResponse()
    if not admin_caller():
        listen(resp)
        return voice_twiml(resp)
    action = "ban" if request.args.get("action") == "ban" else "add"
    phone = store.normalize_phone("+" + request.args.get("phone", ""))
    choice = request.form.get("Digits", "")
    if choice == "2":
        return ask_for_number(resp, action)
    if choice != "1" or not phone or phone in OWNER_NUMBERS:
        return admin_menu(resp, "Cancelled. Nothing changed.")
    if action == "add":
        welcomed = add_member(phone, "", request.form.get("To", ""))
        done = f"Added {spoken_number(phone)}." + (" I texted them." if welcomed else "")
    else:
        ban_number(phone)
        done = f"Banned {spoken_number(phone)}. They won't get replies, and their calls are rejected."
    return admin_menu(resp, done)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
