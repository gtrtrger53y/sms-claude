# Claude over SMS (friends edition)

One private phone number that you and the friends you add can text or call to talk with Claude. It isn't open to the public. Every phone number is its own little account with:

- **its own conversation** (the last 20 back-and-forths),
- **its own memory** (things it told Claude to remember, like "I'm vegetarian"),
- **its own Gmail**, if that person chooses to connect it.

Everything is saved in a Postgres database (a free Neon database works), so nothing resets when the app redeploys.

Only numbers you add reach Claude. Anyone else who texts gets one short reply saying it's private, and you get a text telling you who it was, so adding a friend is one text.

## Commands

**For you (the owner), texted to the service number:**

| Text | What it does |
|---|---|
| `ADD +15551234567` | Lets that number in. Add a name after it if you like: `ADD +15551234567 Sam`. If they texted before, they get a "You're in!" text. |
| `REMOVE +15551234567` | Takes that number out. |
| `BAN +15551234567` | Blocks that number completely: its texts get no reply and its calls are rejected. `ADD` it again to undo. |
| `LIST` | Shows everyone who's in. |
| `WAITING` | Shows numbers that texted but haven't been added. |

**On a phone call (owner only):** press 8 five times (`88888`) while Claude is listening (after it finishes talking). That opens the admin menu: 1 to add a number, 2 to ban one, 3 to hear your members, 4 to hear numbers waiting to join, star to go back to Claude. Type the number on the keypad and press pound; Claude reads it back and you press 1 to confirm. For anyone else, `88888` does nothing.

**For you and your friends:**

| Text | What it does |
|---|---|
| `HELP` | Lists these commands. |
| `RESET` | Clears the conversation and starts fresh (memory stays). |
| `MEMORY` | Shows what Claude has saved about you. |
| `FORGET ALL` | Deletes what Claude has saved about you. |
| `CONNECT EMAIL` | Texts a one-time link to a page where you connect your Gmail. |
| `DISCONNECT EMAIL` | Removes your email connection. |
| `DELETE MY DATA` | Deletes your chats, memory and email connection. |
| `STOP` | Stops all texts (Twilio handles this). |

People can also just say "remember that my mom's birthday is May 3" and Claude saves it.

## Upgrading from the first version

1. Replace the files in your GitHub repo with the ones in this folder (`app.py`, `gmail.py`, `pages.py`, `Procfile`, `requirements.txt`, `README.md`) and add the new `store.py`.
2. In Render, open your service, go to **Environment**, and make sure these are set:
   - `DATABASE_URL`: your Neon connection string (you already added this).
   - `SECRET_KEY`: click **Add Environment Variable**, type `SECRET_KEY` as the name, and click **Generate** for the value. This encrypts friends' email passwords. Don't change it later, or everyone has to reconnect their email.
   - `OWNER_NUMBER`: your own cell number, like `+15551234567`. If you have two phones, separate them with a comma: `+15551234567,+15559876543`. If you leave it out, the first number in `ALLOWED_NUMBERS` counts as yours.
   - `TWILIO_ACCOUNT_SID`: if it isn't there yet. It's needed to text you about new numbers, to send "You're in!" texts, and to send slow answers.
   - Keep `ANTHROPIC_API_KEY`, `TWILIO_AUTH_TOKEN`, and the rest as they are. `ALLOWED_NUMBERS` still works; those numbers are always let in.
3. In Render, under **Settings → Start Command**, use:
   `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 60 --workers 1 --threads 8`
   Keep `--workers 1`: email drafts and phone calls in progress are kept in the app's memory.
4. Save. Render redeploys by itself after the GitHub update. The database tables are created automatically on the first start.

Old conversations from the first version aren't copied over (they were lost on every redeploy anyway).

## Optional settings

| Setting | Default | What it does |
|---|---|---|
| `DAILY_LIMIT` | `50` | Most texts one friend can send in 24 hours, so nobody runs up your bill. `0` means no limit. You (the owner) have no limit. |
| `PAY_LINK` | none | Optional, if friends chip in: a payment page such as a Stripe Payment Link, included in the one reply to numbers that aren't added yet. |
| `PRICE_TEXT` | none | What it costs, like `$5/month`. Shown with the link and on the website. |
| `SERVICE_NAME` | `Claude SMS Assistant` | The name used in texts and on the website. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | `claude-haiku-5-5` is much cheaper and faster; `claude-opus-5-5` is smartest. |
| `VOICE_MODEL`, `VOICE_NAME` | Sonnet 5.5, `Polly.Joanna-Neural` | For phone calls. |
| `VOICE_RATE` | `120%` | How fast the call voice talks. `100%` is normal speed; try `110%` to `130%`. |
| `ADMIN_CODE` | `88888` | Keys that open the admin menu on a call. |
| `WEB_SEARCH` | `on` | `off` stops web searches (each costs about a cent). |
| `TIMEZONE` | `America/New_York` | So Claude knows the local date and time. |
| `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | none | Your own Gmail. Only your number uses these; friends connect their own with `CONNECT EMAIL`. |
| `PUBLIC_URL` | Render fills it in | The app's address, used in email-connect links. |

## If friends chip in (optional)

You don't need payments at all. If friends want to share the cost, a Stripe Payment Link has no monthly fee (2.9% + 30¢ per payment); set `PAY_LINK` and `PRICE_TEXT`. Stripe needs an adult as the account holder.

## Before friends start using it

- **Update your Twilio verification wording.** It currently describes a service only for your own use. Change it to "a private assistant for the owner and a few friends they add, who opt in by texting first." The website pages in this version already say that. If you ever charge people or open it up, the verification needs updating again.
- **Follow Anthropic's usage policy.** You're responsible for how friends use it with your API key, and they should know they're talking to an AI.

## How friends connect Gmail

1. Your friend texts `CONNECT EMAIL`.
2. They get a link that works once, for 15 minutes, to a page on your site.
3. The page shows how to make a Gmail app password, and they type their Gmail address and app password there. Nobody ever texts a password.
4. The app checks that Google accepts it, then saves it encrypted with your `SECRET_KEY`.

Claude can then search, read, and summarize their email, and send email only after reading the draft back and getting a yes. It never deletes email.

Holding friends' email access is a real responsibility, so only connect people who want it. A "Sign in with Google" button would be nicer, but Google reviews apps that read Gmail before letting the public use them, so that's a later step if you ever want it.

## What it costs per month

| Thing | Cost |
|---|---|
| Render | $0 on the Free instance (sleeps when idle; the first text after a nap can be slow). $7/month for an always-on instance. The $25 Pro plan isn't needed. |
| Neon | $0 on the Free plan (1 GB, plenty for texts). It naps after 5 idle minutes, so the first text after a quiet spell is about a second slower. |
| Twilio | $2.15/month for a toll-free number ($1.15 local), plus about 1.2¢ per text segment in or out (carrier fees included). A segment is about 153 characters, so a 300-character reply is 2 segments. |
| Claude API | About 1–2¢ per reply on Sonnet 5.5, plus about 1¢ per web search. Haiku 5.5 costs a small fraction of that. |
| Stripe | No monthly fee; 2.9% + 30¢ per payment. |

Roughly 5¢ per back-and-forth, so a friend who sends 100 texts a month costs you about $5. Prices were checked in November 2026.

## The website

`/` describes the service, `/terms` is the Terms of Service, `/privacy` is the Privacy Policy. Use these in the Twilio verification form.

## Running it on your own computer (optional)

Without `DATABASE_URL` it saves to a local file, `conversations.db`.

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export ALLOWED_NUMBERS=+15551234567
python app.py
```

Without `TWILIO_AUTH_TOKEN` the app skips Twilio's signature check, so only do that on your own computer.
