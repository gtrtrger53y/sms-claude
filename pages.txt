"""Web pages: what the service is, terms, privacy, and the one-time email connect page.

Carriers reviewing a toll-free or 10DLC registration look for the first three.
The owner's name, contact email, and phone number come from environment
variables so nothing personal is stored in the code.
"""

import imaplib
import os

from flask import Blueprint, abort, render_template_string, request

import gmail
import store

SERVICE_NAME = os.environ.get("SERVICE_NAME", "Claude SMS Assistant")
OWNER_NAME = os.environ.get("OWNER_NAME", "the owner of this service")
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
SMS_NUMBER = os.environ.get("SMS_NUMBER", "")
# Optional: a payment page (for example a Stripe Payment Link) and what it costs.
PAY_LINK = os.environ.get("PAY_LINK", "").strip()
PRICE_TEXT = os.environ.get("PRICE_TEXT", "").strip()

pages = Blueprint("pages", __name__)

LAYOUT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{{ title }} | {{ service }}</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; line-height: 1.6; color: #222;
         background: #fff; max-width: 680px; margin: 0 auto; padding: 24px 16px 48px; }
  h1 { font-size: 1.6rem; margin-bottom: 0.25rem; }
  h2 { font-size: 1.15rem; margin-top: 1.75rem; }
  nav a { margin-right: 16px; }
  .box { background: #f4f4f6; border-radius: 8px; padding: 12px 16px; }
  .err { background: #fdecec; border-radius: 8px; padding: 12px 16px; }
  label { display: block; margin-top: 14px; font-weight: 600; }
  input { width: 100%; box-sizing: border-box; font-size: 1rem; padding: 10px; margin-top: 4px;
          border: 1px solid #bbb; border-radius: 6px; }
  button { margin-top: 18px; font-size: 1rem; padding: 10px 18px; border: 0; border-radius: 6px;
           background: #222; color: #fff; }
  footer { margin-top: 40px; font-size: 0.9rem; color: #666; }
</style>
</head>
<body>
<nav><a href="/">Home</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a></nav>
{{ body|safe }}
<footer>{{ service }} is operated by {{ owner }}{% if email %} · <a href="mailto:{{ email }}">{{ email }}</a>{% endif %}</footer>
</body>
</html>"""

HOME = """
<h1>{{ service }}</h1>
<p>{{ service }} is a private text-message assistant. When a member sends a text message to
{% if number %}<strong>{{ number }}</strong>{% else %}our number{% endif %}, it replies with an
answer written by Claude, an AI assistant made by Anthropic. You can also call the number and talk.</p>

<h2>Who can use it</h2>
<p>This is a private service run by {{ owner }} for themselves and a few friends they add. It is not
offered to the public. Only phone numbers the operator has added can use it.
{% if pay %}Members share the cost{% if price %} ({{ price }}){% endif %} through a secure payment page.{% endif %}
If any other number texts, it gets a single reply saying the service is private, and nothing else.</p>

<h2>How you opt in</h2>
<p>You opt in by sending the first text message to the number yourself. The service never sends a
message you did not ask for: every message it sends is a reply to a text you just sent, or a
one-time link you asked for. No marketing or promotional messages are ever sent.</p>

<div class="box">
<p><strong>Message frequency</strong> varies; you get one reply for each message you send.<br>
<strong>Message and data rates may apply.</strong><br>
Text <strong>STOP</strong> to opt out at any time. Text <strong>HELP</strong> for help.<br>
Text <strong>RESET</strong> to clear your conversation and start fresh.</p>
</div>

<h2>Example</h2>
<p class="box">You: What's a good substitute for buttermilk?<br>
{{ service }}: Mix 1 cup of milk with 1 tablespoon of lemon juice or vinegar and let it sit for 5 minutes.</p>

<p>See the <a href="/terms">Terms</a> and <a href="/privacy">Privacy Policy</a>.</p>
"""

TERMS = """
<h1>Terms of Service</h1>
<p>These terms apply to the {{ service }} text-message program operated by {{ owner }}.</p>

<h2>The program</h2>
<p>{{ service }} answers text messages and phone calls from members with replies written by Claude,
an AI assistant. Each message it sends is a reply to a message you sent, or a link you asked for.
It does not send marketing messages.</p>

<h2>Opting in and out</h2>
<p>You opt in by texting the number yourself. Text <strong>STOP</strong> at any time to stop receiving
messages; you will get one confirmation and no further messages. Text <strong>START</strong> to opt
back in. Text <strong>HELP</strong> for help{% if email %}, or email <a href="mailto:{{ email }}">{{ email }}</a>{% endif %}.</p>

<h2>Costs</h2>
<p>{% if pay %}Membership{% if price %} costs {{ price }}{% endif %} and is paid through a secure payment
page.{% else %}The service is free for members.{% endif %}
<strong>Message and data rates may apply</strong> from your mobile carrier.
Message frequency varies with how many messages you send. Members may have a daily message limit.</p>

<h2>Email</h2>
<p>Members can choose to connect their own Gmail. The assistant can then search, read, and summarize
their email, and send an email only after reading the draft back and getting a yes. It never deletes
email. Text <strong>DISCONNECT EMAIL</strong> to remove the connection at any time.</p>

<h2>AI answers</h2>
<p>Replies are generated by AI and can be wrong. Do not rely on them for medical, legal, financial,
or emergency decisions. In an emergency, call 911. Use of the assistant must follow
Anthropic's usage policy.</p>

<h2>Carriers</h2>
<p>Mobile carriers are not liable for delayed or undelivered messages.</p>
"""

PRIVACY = """
<h1>Privacy Policy</h1>
<p>This policy explains how the {{ service }} text-message program, operated by {{ owner }}, handles
your information.</p>

<h2>What we collect</h2>
<p>Your phone number, the text of the messages you send and receive, and short notes you ask the
assistant to remember, so it can keep your conversation going. Each phone number's information is kept
separate from every other number's. If you connect Gmail, we store your Gmail address and an app
password, encrypted.</p>

<h2>How it is used</h2>
<p>Your messages are sent to Anthropic, the maker of Claude, only to generate replies, and to Twilio,
only to deliver the text messages and calls. Email is read only when you ask the assistant about it.
Nothing is used for anything else.</p>

<h2>Sharing</h2>
<p><strong>No mobile information will be shared with third parties or affiliates for marketing or
promotional purposes.</strong> Text messaging originator opt-in data and consent will not be shared
with any third parties.</p>

<h2>Deleting your data</h2>
<p>Text <strong>RESET</strong> to delete your saved conversation, <strong>FORGET ALL</strong> to delete
what the assistant remembers about you, <strong>DISCONNECT EMAIL</strong> to remove your email
connection, or <strong>DELETE MY DATA</strong> to delete all of it. Text <strong>STOP</strong> to stop all
messages.</p>

{% if email %}<h2>Contact</h2>
<p>Questions: <a href="mailto:{{ email }}">{{ email }}</a></p>{% endif %}
"""

CONNECT_FORM = """
<h1>Connect your Gmail</h1>
<p>This lets {{ service }} check and send email for the phone number ending in <strong>{{ last4 }}</strong>.
It can search and read your mail, and it only sends an email after reading it back to you and getting a yes.
It never deletes anything.</p>

<div class="box">
<p><strong>Get an app password from Google (2 minutes):</strong></p>
<ol>
<li>Turn on 2-Step Verification at <a href="https://myaccount.google.com/security" target="_blank" rel="noopener">myaccount.google.com/security</a>.</li>
<li>Open <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">myaccount.google.com/apppasswords</a>,
type a name like <em>Claude phone</em>, and tap <strong>Create</strong>.</li>
<li>Copy the 16-letter password Google shows and paste it below.</li>
</ol>
<p>This is not your normal Google password. You can cut off access any time by deleting the app
password on that same Google page, or by texting DISCONNECT EMAIL.</p>
</div>

{% if error %}<p class="err">{{ error }}</p>{% endif %}

<form method="post" autocomplete="off">
<label for="address">Gmail address</label>
<input id="address" name="address" type="email" required value="{{ address }}" placeholder="you@gmail.com">
<label for="password">App password (16 letters)</label>
<input id="password" name="password" type="password" required autocomplete="new-password">
<button type="submit">Connect</button>
</form>
<p style="font-size:0.9rem;color:#666">This link works once and expires 15 minutes after it was sent.</p>
"""

CONNECT_DONE = """
<h1>Gmail connected</h1>
<p>{{ address }} is now connected to the phone number ending in <strong>{{ last4 }}</strong>.
Go back to your texts and try "Do I have any new emails?"</p>
"""

CONNECT_EXPIRED = """
<h1>This link has expired</h1>
<p>Text <strong>CONNECT EMAIL</strong> to {{ service }} to get a new one.</p>
"""


def ctx():
    return dict(service=SERVICE_NAME, owner=OWNER_NAME, email=CONTACT_EMAIL, number=SMS_NUMBER,
                pay=bool(PAY_LINK), price=PRICE_TEXT)


def render(title, body, **extra):
    context = ctx()
    context.update(extra)
    return render_template_string(LAYOUT, title=title, body=render_template_string(body, **context),
                                  **ctx())


@pages.get("/")
def home():
    return render("Home", HOME)


@pages.get("/terms")
def terms():
    return render("Terms of Service", TERMS)


@pages.get("/privacy")
def privacy():
    return render("Privacy Policy", PRIVACY)


@pages.route("/connect/<token>", methods=["GET", "POST"])
def connect_email(token):
    headers = {"Cache-Control": "no-store"}
    phone = store.peek_link_token(token, "gmail")
    if not phone:
        return render("Link expired", CONNECT_EXPIRED), 410, headers
    if not gmail.can_store_passwords():
        abort(503)
    last4 = phone[-4:]
    if request.method == "GET":
        return render("Connect Gmail", CONNECT_FORM, last4=last4, address="", error=""), 200, headers

    address = request.form.get("address", "").strip()
    password = request.form.get("password", "")
    error = ""
    if not address or "@" not in address:
        error = "Please enter your full Gmail address."
    elif len(gmail.clean_password(password)) != 16:
        error = "An app password is 16 letters. Copy it from myaccount.google.com/apppasswords."
    else:
        try:
            gmail.check_login(address, password)
        except imaplib.IMAP4.error:
            error = ("Google didn't accept that address and app password. Check both and try again. "
                     "Your normal Google password won't work here.")
        except OSError:
            error = "Couldn't reach Gmail just now. Please try again in a minute."
    if error:
        return render("Connect Gmail", CONNECT_FORM, last4=last4, address=address, error=error), 200, headers

    # Use the link up only once the details work, so a typo doesn't waste it.
    if store.use_link_token(token, "gmail") != phone:
        return render("Link expired", CONNECT_EXPIRED), 410, headers
    store.set_gmail(phone, address, gmail.encrypt(password))
    return render("Gmail connected", CONNECT_DONE, last4=last4, address=address), 200, headers
