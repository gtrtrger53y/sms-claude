
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
<p>{{ service }} is a text-message assistant. When a member sends a text message to
{% if number %}<strong>{{ number }}</strong>{% else %}our number{% endif %}, it replies with an
answer written by Claude, an AI assistant made by Anthropic. You can also call the number and talk.</p>

<h2>Who can use it</h2>
<p>The service is open to anyone who signs up for a membership{% if price %} ({{ price }}){% endif %}.
To join, text the number above and follow the payment instructions{% if pay %}
using the secure <a href="{{ pay }}">payment page</a>{% endif %}.
After payment is verified, {{ owner }} will activate your phone number and you will receive
a confirmation text. Access is not active until your number has been added.</p>

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
<p>These Terms of Service apply to the {{ service }} service, operated by {{ owner }}.
By using the service, you agree to these terms.</p>

<h2>The service</h2>
<p>{{ service }} is an AI-powered assistant that allows members to send text messages
and make phone calls to receive responses generated with Claude, an AI assistant provided
by Anthropic. Members may send questions, request information, and send photos for the
assistant to analyze. Features may change over time.</p>

<h2>Membership and activation</h2>
<p>The service is available to people who request membership. To sign up, text the service
number and follow the instructions provided. {% if pay %}Any membership payment must be made
through the payment page or instructions provided. {% endif %}The operator verifies payment
and activates membership manually. Sending a text or making a payment does not mean that
access has already been activated. Access begins after the operator adds your phone number
to the service.</p>

{% if price %}
<p>The advertised membership price is {{ price }}. Any applicable price will be shown
before payment. Payment processing is handled by the payment provider, not by the
text-message service itself.</p>
{% endif %}

<h2>Text message consent and opt-out</h2>
<p>You opt in by voluntarily sending the first text message to the service number.
The service sends responses to messages you initiate and service-related messages,
such as an activation confirmation or a link you requested. It does not send marketing
or promotional text messages.</p>

<p>Message frequency varies based on how often you contact the service. You generally
receive a response for each message you send. Message and data rates may apply depending
on your mobile carrier and plan.</p>

<p>You can opt out of text messages at any time by replying <strong>STOP</strong>.
After opting out, you will no longer receive text messages unless you opt in again.
Reply <strong>HELP</strong> for assistance. You may also contact
{% if email %}<a href="mailto:{{ email }}">{{ email }}</a>{% else %}the service operator{% endif %}
for support.</p>

<h2>Email connection</h2>
<p>Members may choose to connect a Gmail account. If connected, the assistant can access
email to search, read, and summarize messages when requested. The assistant will only
send an email after showing you the draft and receiving your confirmation. The service
does not intentionally delete your emails. You can disconnect Gmail at any time by
texting <strong>DISCONNECT EMAIL</strong>.</p>

<h2>AI-generated responses</h2>
<p>Responses are generated using artificial intelligence and may be inaccurate,
incomplete, or out of date. You are responsible for checking important information.
Do not rely on the service for medical, legal, financial, emergency, or other
high-stakes decisions. In an emergency, contact the appropriate emergency services.</p>

<h2>Acceptable use</h2>
<p>You agree not to use the service for unlawful purposes, to harass others, to send
unwanted messages, or to interfere with the service or its systems. Access may be
suspended or removed if the service is misused.</p>

<h2>Availability</h2>
<p>The service is provided on an as-available basis. Replies may be delayed or unavailable
because of maintenance, technical problems, AI provider outages, mobile carrier issues,
or other circumstances outside the operator's control.</p>

<h2>Mobile carriers</h2>
<p>Mobile carriers are not liable for delayed or undelivered messages.</p>

<h2>Contact</h2>
<p>For questions or support, contact
{% if email %}<a href="mailto:{{ email }}">{{ email }}</a>{% else %}the service operator{% endif %}.</p>
"""

PRIVACY = """
<h1>Privacy Policy</h1>
<p>This Privacy Policy explains how {{ service }}, operated by {{ owner }}, collects,
uses, and shares information when you use the service.</p>

<h2>Information we collect</h2>
<p>Depending on how you use the service, we may collect and store:</p>
<ul>
<li>Your mobile phone number and basic account information.</li>
<li>Text messages you send and the responses sent by the service.</li>
<li>Conversation history and notes or memory you ask the assistant to save.</li>
<li>Photos you send to the assistant for analysis.</li>
<li>Information related to phone calls, including conversation transcripts.</li>
<li>Images generated by the service in response to your requests.</li>
<li>If you connect Gmail, your Gmail address and an encrypted Gmail app password.</li>
<li>Basic technical information, such as account creation and last-use timestamps.</li>
</ul>
<p>Information associated with one phone number is kept separate from information
associated with other phone numbers.</p>

<h2>How we use information</h2>
<p>We use this information to operate the service, identify members, respond to messages
and calls, maintain conversation context, provide requested features, manage Gmail
connections, provide customer support, and protect the service from misuse.</p>

<h2>Service providers</h2>
<p>To provide the service, certain information may be processed by third-party providers:</p>
<ul>
<li><strong>Anthropic:</strong> Message content and other information you submit may be
sent to Anthropic to generate AI responses.</li>
<li><strong>Twilio:</strong> Phone numbers and message or call information are processed
by Twilio to send and receive text messages and phone calls.</li>
<li><strong>Google:</strong> If you connect Gmail, information is accessed through Gmail
to perform the email actions you request.</li>
<li><strong>Payment provider:</strong> If membership payments are offered, payment
information is handled by the payment provider through its payment page.</li>
</ul>
<p>These providers process information as needed to provide their services and under
their own applicable privacy policies and terms.</p>

<h2>Sharing and mobile information</h2>
<p>We do not sell your personal information. We do not share mobile information with
third parties or affiliates for their marketing or promotional purposes.</p>

<p><strong>All the above categories exclude text messaging originator opt-in data and
consent; this information won’t be shared with any third parties.</strong></p>

<h2>Data storage and security</h2>
<p>We use reasonable measures designed to protect stored information. Gmail app passwords
are encrypted before being stored. No method of electronic storage or transmission is
completely secure, and we cannot guarantee absolute security.</p>

<p>Generated images may be stored temporarily and are designed to expire after
approximately 24 hours. Other information may remain stored for as long as needed
to operate the service, maintain accounts, meet legal obligations, and resolve disputes.</p>

<h2>Managing and deleting your information</h2>
<p>You can use the following commands from the phone number associated with your account:</p>
<ul>
<li><strong>RESET:</strong> Clears your saved text-message conversation history.
This does not clear phone-call conversation history.</li>
<li><strong>FORGET ALL:</strong> Clears the notes and memory the assistant has saved about you.</li>
<li><strong>DISCONNECT EMAIL:</strong> Removes your saved Gmail address and encrypted app password.</li>
<li><strong>DELETE MY DATA:</strong> Deletes your stored text-message and phone-call
conversation history, saved memory, Gmail connection information, and pending Gmail
connection links.</li>
<li><strong>STOP:</strong> Opts you out of receiving text messages.</li>
</ul>

<p>DELETE MY DATA does not automatically remove your phone number, account status,
account timestamps, or all technical records from the service database. Information
already processed or retained by third-party providers is subject to those providers'
own retention policies.</p>

<h2>Message frequency and rates</h2>
<p>Message frequency varies based on how often you contact the service. Message and
data rates may apply. Reply <strong>STOP</strong> to opt out or <strong>HELP</strong>
for assistance.</p>

<h2>Contact</h2>
<p>For privacy questions or requests, contact
{% if email %}<a href="mailto:{{ email }}">{{ email }}</a>{% else %}the service operator{% endif %}.</p>
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
