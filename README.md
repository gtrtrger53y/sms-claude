# Claude over SMS

Text a phone number, get a reply from Claude. Each phone number gets its own ongoing conversation (the last 20 back-and-forths are remembered). Text **RESET** to start fresh, or **HELP** for a reminder.

## How it works

1. Someone texts your Twilio phone number.
2. Twilio forwards the text to this small web app.
3. The app sends the conversation to Claude and gets a reply.
4. Twilio texts the reply back.

## What you need (about 30 minutes, one time)

| Thing | Where | Rough cost |
|---|---|---|
| Anthropic API key | console.anthropic.com | Pay per use, usually cents per day of chatting |
| Twilio account + phone number | twilio.com | About $1.15/month for the number plus about $0.008 per text |
| A place to run the app | render.com (or Railway, Fly.io) | Free tier works to try it; about $7/month to keep it always on |

## Step 1: Get an Anthropic API key

1. Go to **console.anthropic.com** and sign up or log in.
2. Add a payment method under **Billing** and buy a small amount of credit (for example $5).
3. Go to **API Keys**, click **Create Key**, and copy it somewhere safe. It starts with `sk-ant-`.

## Step 2: Get a Twilio phone number

1. Sign up at **twilio.com**.
2. In the Twilio Console, go to **Phone Numbers → Manage → Buy a number** and buy one with SMS capability.
3. On the Console home page, copy your **Auth Token** (click to reveal it).
4. **US numbers only:** US carriers require registration before texts go through. In the console, either register for **A2P 10DLC** (pick "Sole Proprietor" if it's just you) or buy a **toll-free** number and complete **Toll-Free Verification**. Approval can take a few days. Until then, texts may be blocked. (Trial accounts can only text numbers you've verified, and replies start with "Sent from your Twilio trial account".)

## Step 3: Put the app online (Render example)

1. Put the files in this folder into a GitHub repository. (If you create an empty repo and tell me its name, I can upload them for you.)
2. Sign up at **render.com** and click **New → Web Service**, then pick that repository.
3. Settings:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 60`
4. Under **Environment**, add these variables:
   - `ANTHROPIC_API_KEY` = your key from Step 1
   - `TWILIO_AUTH_TOKEN` = your token from Step 2
   - `ALLOWED_NUMBERS` = your own cell number in the form `+15551234567` (add more separated by commas). This stops strangers from using up your credit.
   - `OWNER_NAME` = your full name, `CONTACT_EMAIL` = your email, `SMS_NUMBER` = your Twilio number. These fill in the public website pages below.
5. Click **Deploy**. When it's done, Render shows your app's address, like `https://claude-sms-abcd.onrender.com`. Opening it in a browser shows the service's website.

## The website (for Twilio's verification form)

Phone carriers want a website that explains the texting service before they approve your number. The app has one built in:

- `https://your-app.onrender.com/` describes the service, how people opt in, and STOP/HELP
- `https://your-app.onrender.com/terms` is the Terms of Service
- `https://your-app.onrender.com/privacy` is the Privacy Policy

Use these addresses in the Toll-Free Verification or A2P 10DLC form.

## Step 4: Connect Twilio to the app

1. In Twilio, go to **Phone Numbers → Manage → Active numbers** and click your number.
2. Under **Messaging → A message comes in**, choose **Webhook**, method **HTTP POST**, and paste your app address with `/sms` on the end, for example `https://claude-sms-abcd.onrender.com/sms`.
3. Save.

Now text your Twilio number from your phone. Claude should reply within a few seconds.

## Good to know

- **Render's free tier falls asleep** after 15 minutes of no use. The first text after a nap can take too long and get no reply; just send it again. The paid tier avoids this.
- **Memory resets on redeploy** on most free hosts, because the conversation file isn't kept. Attach a persistent disk (Render paid) if you want history to survive.
- **Changing the model:** set `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`). `claude-haiku-5-5` is cheaper and faster; `claude-opus-5-5` is smartest but slower and pricier.
- **Long replies** are capped at 1,500 characters (Twilio's per-message limit is 1,600). Claude is told to keep answers short and plain-text.
- **Security:** the app checks Twilio's signature on every request, so only Twilio can talk to it. Never share your API key or Auth Token.

## Running it on your own computer (optional, for tinkering)

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export TWILIO_AUTH_TOKEN=...
python app.py
```

Then use a tunnel such as `ngrok http 5000` and paste the ngrok address plus `/sms` into Twilio.
