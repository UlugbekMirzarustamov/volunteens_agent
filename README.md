# Volunteens Opportunity Agent

Every day this agent:
1. searches the web for opportunities that are open now (research programs, fellowships, grants, competitions, summer schools, teen accelerators like Lumiere and TKS),
2. checks each one on its official page and skips anything already closed or already posted,
3. posts 2 of them (you can change the number) to your Telegram channel.

It runs free on GitHub Actions, so your laptop can stay off. The only cost is the Claude API, which comes to roughly a few cents per run.

---

## One-time setup (about 15 minutes)

### 1. Create the Telegram bot
1. In Telegram, open **@BotFather**, send `/newbot`, and follow the steps.
2. Copy the **bot token** it gives you (it looks like `123456:ABC...`). Keep it secret.
3. Open your **Volunteens** channel → *Administrators* → *Add admin* → search for your bot → give it the **Post messages** permission.
4. Your channel ID is its public username, for example `@volunteens`. If the channel is private, forward one of its posts to **@userinfobot** to get a numeric ID like `-100123...`.

### 2. Get a Claude API key
Go to **console.anthropic.com** → *API Keys* → create a key, then add some credit under *Billing* ($5 is enough to start).

### 3. Put the code on GitHub
1. Create a free account at github.com, then click **New repository** (choose *Private*).
2. Click **uploading an existing file** and drag in everything from this folder, **including the `.github` folder**. (On a Mac, press `Cmd+Shift+.` in Finder to see hidden folders.)

### 4. Add your secrets
In the repository go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Claude API key |
| `TELEGRAM_BOT_TOKEN` | your bot token |
| `TELEGRAM_CHAT_ID` | `@volunteens` (or the numeric ID) |

### 5. Test it
Go to the **Actions** tab → **Post opportunities** → **Run workflow**. In about 2–5 minutes, posts appear in your channel.

After that it runs on its own every day at 09:00 UTC.

---

## Optional settings
Add these under **Settings → Secrets and variables → Actions → Variables**:

- `POSTS_PER_RUN`: how many posts per run (default `2`).
- `POST_MODE` = `review`: the bot sends posts **to you privately** instead of to the channel, so you can check them and forward the good ones. This is recommended for the first week. For this mode, also add a secret `REVIEW_CHAT_ID` with your own user ID (from @userinfobot), and send your bot `/start` once.

To change the posting time, edit the `cron` line in `.github/workflows/daily.yml`. For example, `0 6,15 * * *` posts twice a day, at 06:00 and 15:00 UTC.

## Good to know
- `posted.json` is the agent's memory of what it already posted, which stops repeats. The workflow updates it automatically.
- The AI is told to verify deadlines on the official site, but it can still make mistakes. Glance at the posts now and then, or use review mode.
- To see why a run failed, open the **Actions** tab and click on the failed run.
