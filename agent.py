"""
Volunteens opportunity agent.

Each run:
  1. Reads the Volunteens channel (https://t.me/s/Volunteensuz) to see what was
     already posted, plus the bundled history of all past posts.
  2. Haiku 5.5 (with live web search) finds NEW international opportunities and
     verifies the facts on official pages.
  3. Haiku 5.5 (no tools) writes each post in Uzbek from those facts, in the
     channel's own template (examples in style_examples.json).
  4. Sends the posts to the channel (or privately to you in "review" mode).

Settings come from environment variables (see README.md):
  ANTHROPIC_API_KEY    - your Claude API key
  TELEGRAM_BOT_TOKEN   - from @BotFather
  TELEGRAM_CHAT_ID     - channel, e.g. @Volunteensuz
  POST_MODE            - "auto" (post to channel) or "review" (send to REVIEW_CHAT_ID)
  REVIEW_CHAT_ID       - your own Telegram user id (only for review mode)
  POSTS_PER_RUN        - how many opportunities per run (default 2)
  DRY_RUN              - "1" to print posts instead of sending
"""

import datetime as dt
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import anthropic
import requests

HERE = Path(__file__).parent
POSTED_FILE = HERE / "posted.json"
HISTORY_FILE = HERE / "channel_history.json"
EXAMPLES_FILE = HERE / "style_examples.json"
CHANNEL = "Volunteensuz"
SEARCH_MODEL = "claude-haiku-5-5"  # cheap: does the web search and fact-checking
WRITER_MODEL = "claude-sonnet-5-5"  # better Uzbek; writes the posts (no tools, so few tokens)

FOOTER = ('📝 <a href="https://t.me/Volunteensuz">Telegram</a> | '
          '💻 <a href="https://www.instagram.com/volunteensuz">Instagram</a>')

POSTS_PER_RUN = int(os.environ.get("POSTS_PER_RUN", "2"))
POST_MODE = os.environ.get("POST_MODE", "auto").strip().lower()
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

SCHEMA = {
    "type": "object",
    "properties": {
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Official name in English"},
                    "official_url": {"type": "string"},
                    "deadline": {"type": "string", "description": "YYYY-MM-DD, or 'Rolling'"},
                    "post_text": {"type": "string", "description": "The full Uzbek post, without the Telegram/Instagram footer"},
                },
                "required": ["name", "official_url", "deadline", "post_text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["opportunities"],
    "additionalProperties": False,
}

FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Official name in English"},
                    "official_url": {"type": "string"},
                    "deadline": {"type": "string", "description": "YYYY-MM-DD, or 'Rolling'"},
                    "facts": {"type": "string", "description": (
                        "Verified facts in English: organizer, what it is, what participants do and get, "
                        "event dates, location, funding, age limit, application fee, eligibility")},
                },
                "required": ["name", "official_url", "deadline", "facts"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["opportunities"],
    "additionalProperties": False,
}


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def fetch_recent_channel_titles() -> list[str]:
    """First lines of the newest ~60 channel posts (includes posts you made by hand)."""
    titles, before = [], None
    for _ in range(3):
        url = f"https://t.me/s/{CHANNEL}" + (f"?before={before}" if before else "")
        try:
            page = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
        except requests.RequestException:
            break
        ids = [int(i) for i in re.findall(rf'data-post="{CHANNEL}/(\d+)"', page)]
        for raw in re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', page, re.S):
            text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", raw))).strip()
            if text:
                titles.append(text.split("\n")[0].strip()[:120])
        if not ids:
            break
        before = min(ids)
    return titles


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _run(client: anthropic.Anthropic, label: str, **kwargs) -> list[dict]:
    """Stream one request (resuming pause_turn) and return its JSON 'opportunities' list."""
    messages = kwargs.pop("messages")
    for _ in range(5):  # resume if the server pauses a long search turn
        with client.beta.messages.stream(messages=messages, **kwargs) as stream:
            response = stream.get_final_message()
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        break

    if response.stop_reason == "refusal":
        raise RuntimeError(f"[{label}] Claude declined the request.")

    print(f"[{label}] model={response.model} stop_reason={response.stop_reason} usage={response.usage}")
    texts = [b.text for b in response.content if b.type == "text"]
    for t in reversed(texts):  # the JSON answer is the last text block
        match = re.search(r"\{.*\}", t, re.S)
        if match:
            try:
                opps = json.loads(match.group(0))["opportunities"]
            except (json.JSONDecodeError, KeyError):
                continue
            if not opps:
                print(f"[{label}] Empty list. Last text from Claude:\n", "\n".join(texts)[-3000:])
            return opps
    raise RuntimeError(f"[{label}] No JSON in response:\n" + "\n".join(texts)[-3000:])


def research_opportunities(client: anthropic.Anthropic, already_titles: list[str]) -> list[dict]:
    """Step 1 (Haiku 5.5): web search + verify facts on official pages. English facts only."""
    today = dt.date.today().isoformat()
    already = "\n".join(f"- {t}" for t in already_titles)

    prompt = f"""Today is {today}. You research opportunities for "Volunteens" (t.me/Volunteensuz), an
Uzbek Telegram channel for young people from Uzbekistan, mostly aged 15-30 (school pupils,
university students, young people). Any event or program young people can join fits: international
forums, summits and youth conferences, MUNs, hackathons, competitions and olympiads, summer/winter
schools and camps, exchange programs, leadership/innovation programs (like TKS), research programs
(like Lumiere, Polygence, RSI), scholarships and grants, volunteering and internships.

TASK: Find {POSTS_PER_RUN + 2} opportunities that are open right now and collect verified facts.

What to look for:
- Mainly opportunities for ages 15-30. A few outside that range are fine, but most must fit it.
- Mix INTERNATIONAL opportunities with events IN UZBEKISTAN (Tashkent and other cities):
  hackathons, IT and startup competitions (e.g. IT Park, tech communities), youth forums,
  MUNs, olympiads, volunteering, trainings and camps for Uzbek youth. Search in Uzbek and Russian
  too (e.g. "xakaton 2026", "yoshlar forumi", "хакатон Ташкент").
- Avoid PhD, postdoc and master's-level opportunities and jobs for experienced professionals.
  At most one per run, and only if it is exceptional.
- Prefer free, fully funded or partially funded opportunities. Mix the types.

Rules:
- Use web search, then open each opportunity's OFFICIAL page to verify the facts.
- The deadline must be after {today} (or genuinely rolling). Skip anything closed.
- People from Uzbekistan must be eligible.
- Never guess a fact (deadline, age, fee). If you can't confirm it, leave it out or pick another opportunity.
- official_url = the official application or info page, not a blog or aggregator.
- In "facts", write in English everything a post needs: organizer, what it is, what participants
  do and get, event dates, location, funding, age limit, application fee, eligibility.
- Do NOT pick anything the channel already posted. Already posted (titles of past posts):
{already}

Return {POSTS_PER_RUN + 2} opportunities in the required JSON format. If you could verify fewer,
return the ones you verified. Never return an empty list: there are always open opportunities."""

    return _run(
        client, "search",
        model=SEARCH_MODEL,
        max_tokens=32000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": FACTS_SCHEMA}},
        tools=[
            # Haiku doesn't support the _20260209 (dynamic filtering) variants
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 8},
            {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 8,
             "max_content_tokens": 6000},
        ],
        messages=[{"role": "user", "content": prompt}],
    )


def write_posts(client: anthropic.Anthropic, facts: list[dict]) -> list[dict]:
    """Step 2 (Sonnet 5.5): turn the verified facts into Uzbek posts in the channel's style."""
    today = dt.date.today().isoformat()
    examples = load_json(EXAMPLES_FILE, [])
    examples_text = "\n\n=====\n\n".join(examples[-5:])
    facts_text = json.dumps(facts, indent=2, ensure_ascii=False)

    prompt = f"""Today is {today}. You are the editor of "Volunteens" (t.me/Volunteensuz), an Uzbek
Telegram channel for young people from Uzbekistan. Below are verified facts about opportunities.
Write one post for each, in natural, fluent Uzbek.

Use ONLY the facts given. Do not invent or add details (deadline, age, fee, dates). Copy name,
official_url and deadline unchanged into your answer.

Writing rules for post_text, which must match the channel's style EXACTLY:
- Write in Uzbek (Latin script), as in the examples below. Keep the official program name in English.
- Line 1: program name + short year like '27 + a country flag emoji if there is one location.
- Then one paragraph (3-5 sentences): who organizes it, what it is, what participants do and get,
  dates/location, and funding.
- Then the short info lines that apply, e.g. "Yosh chegarasi: ...", "Ariza to‘lovi: Yo‘q",
  "💳 Ariza to‘lovi mavjud", "🗓 Tadbir sanasi: ...", "📍 Manzil: ...".
- Then "‼️ So‘nggi muddat: <day>-<Uzbek month name>" (for example 30-sentyabr; add the year if it is not this year).
- Last line exactly: "🔗 Ro‘yxatdan o‘tish uchun: Havola"  (the word Havola becomes the link automatically)
- Do NOT add the Telegram | Instagram footer, hashtags or markdown (**bold**, etc.). It is added automatically.

Examples of real posts from the channel:

{examples_text}

Verified facts:

{facts_text}

Return one entry per opportunity in the required JSON format."""

    return _run(
        client, "write",
        model=WRITER_MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )


def find_opportunities(already_titles: list[str]) -> list[dict]:
    client = anthropic.Anthropic()
    facts = research_opportunities(client, already_titles)
    if not facts:
        return []
    return write_posts(client, facts)


def is_valid(opp: dict, posted: list[dict], titles: list[str]) -> bool:
    name = norm(opp["name"])
    seen_urls = {p["url"].rstrip("/").lower() for p in posted}
    if name in {norm(p["name"]) for p in posted} or opp["official_url"].rstrip("/").lower() in seen_urls:
        return False
    # the name (without year) appears in a past channel title -> probably a repeat
    core = re.sub(r"(19|20)\d\d$", "", name)
    if len(core) >= 6 and any(core in norm(t) for t in titles):
        return False
    if not opp["official_url"].startswith("http"):
        return False
    d = opp["deadline"].strip()
    if d.lower() != "rolling":
        try:
            if dt.date.fromisoformat(d[:10]) < dt.date.today():
                return False
        except ValueError:
            return False
    return True


def format_post(o: dict) -> str:
    body = html.escape(o["post_text"].strip(), quote=False)
    link = f'<a href="{html.escape(o["official_url"], quote=True)}">Havola</a>'
    if "Havola" in body:
        i = body.rfind("Havola")
        body = body[:i] + link + body[i + len("Havola"):]
    else:
        body += f"\n\n🔗 Ro‘yxatdan o‘tish uchun: {link}"
    return f"{body}\n\n{FOOTER}"


def send_telegram(chat_id: str, text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "link_preview_options": {"is_disabled": True}},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def main() -> int:
    posted = load_json(POSTED_FILE, [])
    history = [h["title"] for h in load_json(HISTORY_FILE, [])]
    recent = fetch_recent_channel_titles()
    titles = list(dict.fromkeys(history[-150:] + recent + [p["name"] for p in posted]))

    found = find_opportunities(titles)
    all_titles = history + recent
    fresh = [o for o in found if is_valid(o, posted, all_titles)][:POSTS_PER_RUN]
    print(f"Found {len(found)}, {len(fresh)} new and valid.")
    for o in found:
        if o not in fresh:
            print("Skipped:", o["name"], o["deadline"], o["official_url"])

    target = os.environ["REVIEW_CHAT_ID"] if POST_MODE == "review" else os.environ.get("TELEGRAM_CHAT_ID", "")

    if not fresh and POST_MODE == "review" and not DRY_RUN:
        send_telegram(target, "⚠️ Volunteens agent: bugun yangi imkoniyat topilmadi. GitHub Actions logini tekshiring.")

    for o in fresh:
        post = format_post(o)
        if DRY_RUN:
            print("\n" + "=" * 60 + "\n" + post)
        else:
            send_telegram(target, post)
            time.sleep(3)
        posted.append({"name": o["name"], "url": o["official_url"],
                       "deadline": o["deadline"], "posted_on": dt.date.today().isoformat()})

    if not DRY_RUN:
        POSTED_FILE.write_text(json.dumps(posted, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
