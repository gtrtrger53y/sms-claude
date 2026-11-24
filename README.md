# Claude SMS Assistant

A text-message assistant you join by membership. Members text or call one toll-free number and get answers written by Claude, an AI assistant made by Anthropic. Every member's phone number is its own account with:

- **its own conversation** (the last 20 back-and-forths),
- **its own memory** (things it told Claude to remember, like "I'm vegetarian"),
- **its own Gmail**, if that member chooses to connect it.

Everything is saved in a Postgres database (a free Neon database works), so nothing resets when the app redeploys.

## How people sign up and opt in

1. **Sign up by texting.** Anyone can ask to join by texting the service number. Sending that first text is their opt-in; the service never texts anyone who hasn't texted it first.
2. **Sign-up confirmation.** They get one reply: "{SERVICE_NAME}: thanks for signing up for membership. You'll get a text when your number is activated. Msg frequency varies. Msg & data rates may apply. Reply HELP for help, STOP to opt out." If `PAY_LINK` is set, the reply also includes the payment link (and `PRICE_TEXT`, if set).
3. **Activation.** You (the operator) get a text about the new number and activate it with `ADD`. The member then gets: "{SERVICE_NAME}: you're in! Your membership is active. Text anything to chat. Msg frequency varies. Msg & data rates may apply. Reply HELP for help, STOP to opt out."
4. **After that**, every text the service sends is a reply to a text the member just sent, or a one-time link they asked for. No marketing or promotional texts are ever sent.

**Message frequency varies** (one reply per message sent). **Message and data rates may apply.**

- **HELP** (or INFO) always gets a reply with the service name and the support contact (`CONTACT_EMAIL`; the live site shows Shuamunk95@gmail.com), even from numbers that aren't members yet.
- **STOP** opts a number out right away. Twilio sends the opt-out confirmation itself and blocks further texts; the app stays quiet. Texting START opts back in.

Until a number is activated, Claude doesn't answer it.

## Commands

**For you (the operator), texted to the service number:**

| Text | What it does |
|---|---|
| `ADD +15551234567` | Activates that member. Add a name after it if you like: `ADD +15551234567 Sam`. If they texted before, they get the "you're in" activation text. |
| `REMOVE +15551234567` | Deactivates that member. |
| `BAN +15551234567` | Blocks that number completely: its texts get no reply and its calls are rejected. `ADD` it again to undo. |
| `LIST` | Shows active members. |
| `WAITING` | Shows numbers that signed up but aren't activated yet. |

**On a phone call (operator only):** press 8 five times (`88888`) while Claude is listening (after it finishes talking). That opens the admin menu: 1 to add a number, 2 to ban one, 3 to hear your members, 4 to hear numbers waiting to join, star to go back to Claude. Type the number on the keypad and press pound; Claude reads it back and you press 1 to confirm. For anyone else, `88888` does nothing.

**For members:**

| Text | What it does |
|---|---|
| `HELP` | Lists these commands, with the support contact. |
| `RESET` | Clears the conversation and starts fresh (memory stays). |
| `MEMORY` | Shows what Claude has saved about you. |
| `FORGET ALL` | Deletes what Claude has saved about you. |
| `CONNECT EMAIL` | Texts a one-time link to a page where you connect your Gmail. |
| `DISCONNECT EMAIL` | Removes your email connection. |
| `DELETE MY DATA` | Deletes your chats, memory and email connection. |
| `STOP` | Opts out of all texts (Twilio handles this and sends the confirmation). `START` opts back in. |

People can also just say "remember that my mom's birthday is May 3" and Claude saves it.

## Pictures

- **Send Claude a photo.** Text a picture (with or without words) and Claude looks at it: "what plant is this?", "solve this worksheet problem", "what does this sign say?". Up to 5 photos per text. iPhone HEIC photos work too. Videos, audio and other files get a polite "I can't open that".
- **Claude can text pictures back.** Ask for a diagram, chart, graph, flowchart, simple map or labeled sketch, and Claude draws it and attaches it to the reply. It can't make realistic photos or edit your photos; it says so and offers a simple drawing instead.
- Pictures only work in texts, not on phone calls.
- Drawn pictures are kept for a day at a random link on your site (`/media/<id>.png`) so Twilio can fetch them, then deleted.

## Upgrading from v3 (adding pictures)

1. Replace `app.py`, `store.py`, `requirements.txt` and `README.md` in your GitHub repo with the ones in this folder, and add the new `media.py`. `gmail.py`, `pages.py` and `Procfile` are unchanged.
2. Make sure `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` are set in Render (they already are). Twilio protects photo links with your account login, so the app needs both to download photos people send.
3. Render redeploys by itself after the GitHub update. The new picture table is created automatically.
4. Nothing to change in Twilio: toll-free numbers can send and receive pictures in the US and Canada. Pictures can only be tested once Twilio approves your toll-free registration, same as texts.

## Upgrading from the first version

1. Replace the files in your GitHub repo with the ones in this folder (`app.py`, `gmail.py`, `pages.py`, `Procfile`, `requirements.txt`, `README.md`) and add the new `store.py`.
2. In Render, open your service, go to **Environment**, and make sure these are set:
   - `DATABASE_URL`: your Neon connection string (you already added this).
   - `SECRET_KEY`: click **Add Environment Variable**, type `SECRET_KEY` as the name, and click **Generate** for the value. This encrypts members' email passwords. Don't change it later, or everyone has to reconnect their email.
   - `OWNER_NUMBER`: your own cell number, like `+15551234567`. If you have two phones, separate them with a comma: `+15551234567,+15559876543`. If you leave it out, the first number in `ALLOWED_NUMBERS` counts as yours.
   - `TWILIO_ACCOUNT_SID`: if it isn't there yet. It's needed to text you about new sign-ups, to send activation texts, and to send slow answers.
   - Keep `ANTHROPIC_API_KEY`, `TWILIO_AUTH_TOKEN`, and the rest as they are. `ALLOWED_NUMBERS` still works; those numbers are always let in.
3. In Render, under **Settings → Start Command**, use:
   `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 60 --workers 1 --threads 8`
   Keep `--workers 1`: email drafts and phone calls in progress are kept in the app's memory.
4. Save. Render redeploys by itself after the GitHub update. The database tables are created automatically on the first start.

Old conversations from the first version aren't copied over (they were lost on every redeploy anyway).

## Optional settings

| Setting | Default | What it does |
|---|---|---|
| `DAILY_LIMIT` | `50` | Most texts one member can send in 24 hours, so nobody runs up your bill. `0` means no limit. You (the operator) have no limit. |
| `PAY_LINK` | none | The membership payment page, such as a Stripe Payment Link, included in the sign-up confirmation reply. |
| `PRICE_TEXT` | none | The membership price, shown with the payment link and on the website. |
| `SERVICE_NAME` | `Claude SMS Assistant` | The name used in texts and on the website. |
| `OWNER_NAME` | none | The operator's name shown on the website as who runs the service. |
| `CONTACT_EMAIL` | none | Support email shown on the website and in HELP replies. |
| `SMS_NUMBER` | none | The service number shown on the website. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | `claude-haiku-5-5` is much cheaper and faster; `claude-opus-5-5` is smartest. |
| `VOICE_MODEL`, `VOICE_NAME` | Sonnet 5.5, `Polly.Joanna-Neural` | For phone calls. |
| `VOICE_RATE` | `120%` | How fast the call voice talks. `100%` is normal speed; try `110%` to `130%`. |
| `ADMIN_CODE` | `88888` | Keys that open the admin menu on a call. |
| `WEB_SEARCH` | `on` | `off` stops web searches (each costs about a cent). |
| `TIMEZONE` | `America/New_York` | So Claude knows the local date and time. |
| `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | none | Your own Gmail. Only your number uses these; members connect their own with `CONNECT EMAIL`. |
| `PUBLIC_URL` | Render fills it in | The app's address, used in email-connect links. |

## Membership payments

A Stripe Payment Link has no monthly fee (2.9% + 30¢ per payment). Set `PAY_LINK` and `PRICE_TEXT` and the sign-up reply and website show them. Stripe needs an adult as the account holder.

## Twilio toll-free verification

- **Use case:** a membership text-message assistant. People sign up and opt in by texting the number first; the operator activates them after sign-up. Replies only, no marketing.
- **Opt-in:** the first text a person sends. Point Twilio to `/` for the opt-in description, and include the sign-up confirmation text above as the sample message.
- **Required details** are on the website and in the texts: message frequency varies, message and data rates may apply, HELP for help, STOP to opt out, and links to `/terms` and `/privacy`.
- **Follow Anthropic's usage policy.** You're responsible for how members use it with your API key, and they should know they're talking to an AI.

## How members connect Gmail

1. A member texts `CONNECT EMAIL`.
2. They get a link that works once, for 15 minutes, to a page on your site.
3. The page shows how to make a Gmail app password, and they type their Gmail address and app password there. Nobody ever texts a password.
4. The app checks that Google accepts it, then saves it encrypted with your `SECRET_KEY`.

Claude can then search, read, and summarize their email, and send email only after reading the draft back and getting a yes. It never deletes email.

Holding members' email access is a real responsibility, so only connect people who want it. A "Sign in with Google" button would be nicer, but Google reviews apps that read Gmail before letting the public use them, so that's a later step if you ever want it.

## What it costs per month

| Thing | Cost |
|---|---|
| Render | $0 on the Free instance (sleeps when idle; the first text after a nap can be slow). $7/month for an always-on instance. The $25 Pro plan isn't needed. |
| Neon | $0 on the Free plan (1 GB, plenty for texts). It naps after 5 idle minutes, so the first text after a quiet spell is about a second slower. |
| Twilio | $2.15/month for a toll-free number ($1.15 local), plus about 1.2¢ per text segment in or out (carrier fees included). A segment is about 153 characters, so a 300-character reply is 2 segments. A text with a picture (MMS) costs more: about 3¢ to send and 2 to 3¢ to receive on a toll-free number, carrier fees included. |
| Claude API | About 1–2¢ per reply on Sonnet 5.5, plus about 1¢ per web search. A photo adds about half a cent; a drawn picture adds about a cent. Haiku 5.5 costs a small fraction of that. |
| Stripe | No monthly fee; 2.9% + 30¢ per payment. |

Roughly 5¢ per back-and-forth, so a member who sends 100 texts a month costs you about $5. Prices were checked in November 2026.

## The website

`/` describes the service and how to sign up and opt in, `/terms` is the Terms of Service, `/privacy` is the Privacy Policy. Use these in the Twilio verification form.

## Running it on your own computer (optional)

Without `DATABASE_URL` it saves to a local file, `conversations.db`.

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export ALLOWED_NUMBERS=+15551234567
python app.py
```

Without `TWILIO_AUTH_TOKEN` the app skips Twilio's signature check, so only do that on your own computer.
