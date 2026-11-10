"""
Volunteens opportunity agent.

Each run:
  1. Asks Claude (with live web search) to find NEW international opportunities
     for high-school students: research programs, fellowships, grants,
     competitions, summer schools, accelerators (like Lumiere, TKS, etc.).
  2. Skips anything already posted (tracked in posted.json).
  3. Formats each one as a Telegram post and sends it to the channel
     (or to you privately first, in "review" mode).

Settings come from environment variables (see README.md):
  ANTHROPIC_API_KEY    - your Claude API key
  TELEGRAM_BOT_TOKEN   - from @BotFather
  TELEGRAM_CHAT_ID     - channel, e.g. @volunteens  (or numeric id)
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
MODEL = "claude-opus-5-5"

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
                    "name": {"type": "string"},
                    "organizer": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["Research", "Fellowship", "Grant / Scholarship",
                                 "Competition", "Summer Program", "Accelerator / Incubator",
                                 "Volunteering", "Internship", "Online Course", "Other"],
                    },
                    "official_url": {"type": "string"},
                    "deadline": {"type": "string", "description": "YYYY-MM-DD, or 'Rolling'"},
                    "eligibility": {"type": "string"},
                    "cost": {"type": "string", "description": "e.g. 'Free', 'Fully funded', '$2,000 (financial aid available)'"},
                    "format": {"type": "string", "description": "Online / In-person (city, country) / Hybrid"},
                    "summary": {"type": "string", "description": "2-3 sentences: what students do and get"},
                    "why_apply": {"type": "string", "description": "One punchy sentence on why it's worth it"},
                },
                "required": ["name", "organizer", "category", "official_url", "deadline",
                             "eligibility", "cost", "format", "summary", "why_apply"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["opportunities"],
    "additionalProperties": False,
}


def load_posted() -> list[dict]:
    if POSTED_FILE.exists():
        return json.loads(POSTED_FILE.read_text(encoding="utf-8"))
    return []


def save_posted(posted: list[dict]) -> None:
    POSTED_FILE.write_text(json.dumps(posted, indent=2, ensure_ascii=False), encoding="utf-8")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find_opportunities(posted: list[dict]) -> list[dict]:
    today = dt.date.today().isoformat()
    already = "\n".join(f"- {p['name']} ({p['url']})" for p in posted[-300:]) or "(none yet)"

    prompt = f"""Today is {today}. You find opportunities for "Volunteens", a Telegram channel that helps
high-school students (roughly ages 14-19) from around the world, including developing countries,
build strong extracurricular profiles for university applications.

Find {POSTS_PER_RUN + 2} opportunities that are OPEN NOW or opening soon, of the kind students love,
e.g. research mentorship programs (like Lumiere, Polygence, Pioneer), innovation/leadership programs
(like TKS - The Knowledge Society), fellowships, grants and scholarships, international olympiads and
competitions, essay contests, summer schools, startup accelerators for teens, and
volunteering or internship programs.

Rules:
- Use web search, then open the OFFICIAL page of each opportunity to verify the details.
- The application deadline must be after {today} (or genuinely rolling). Skip anything closed.
- Must accept international students (not limited to one country's citizens).
- Prefer free, fully funded, or need-based-aid opportunities; mix categories.
- Only include facts you verified on the official page. Never guess a deadline; if you cannot
  confirm it, choose a different opportunity.
- official_url must be the opportunity's own page, not a blog or aggregator.
- Do NOT include any of these, which were already posted:
{already}

Return exactly {POSTS_PER_RUN + 2} opportunities in the required JSON format."""

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": prompt}]

    for _ in range(5):  # resume if the server pauses a long search turn
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            tools=[
                {"type": "web_search_20260209", "name": "web_search", "max_uses": 15},
                {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 15},
            ],
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        break

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request.")

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise RuntimeError(f"No JSON in response:\n{text[:2000]}")
    return json.loads(match.group(0))["opportunities"]


def is_valid(opp: dict, posted: list[dict]) -> bool:
    seen_names = {norm(p["name"]) for p in posted}
    seen_urls = {p["url"].rstrip("/").lower() for p in posted}
    if norm(opp["name"]) in seen_names or opp["official_url"].rstrip("/").lower() in seen_urls:
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


def pretty_deadline(d: str) -> str:
    try:
        return dt.date.fromisoformat(d[:10]).strftime("%d %B %Y")
    except ValueError:
        return d


EMOJI = {
    "Research": "🔬", "Fellowship": "🎖", "Grant / Scholarship": "💰", "Competition": "🏆",
    "Summer Program": "☀️", "Accelerator / Incubator": "🚀", "Volunteering": "🤝",
    "Internship": "💼", "Online Course": "💻", "Other": "✨",
}


def format_post(o: dict) -> str:
    e = html.escape
    tag = "#" + re.sub(r"[^A-Za-z]", "", o["category"].split("/")[0])
    return (
        f"{EMOJI.get(o['category'], '✨')} <b>{e(o['name'])}</b>\n"
        f"<i>by {e(o['organizer'])}</i>\n\n"
        f"{e(o['summary'])}\n\n"
        f"🎯 <b>Who can apply:</b> {e(o['eligibility'])}\n"
        f"📍 <b>Format:</b> {e(o['format'])}\n"
        f"💵 <b>Cost:</b> {e(o['cost'])}\n"
        f"⏰ <b>Deadline:</b> {e(pretty_deadline(o['deadline']))}\n\n"
        f"💡 {e(o['why_apply'])}\n\n"
        f"🔗 <a href=\"{e(o['official_url'], quote=True)}\">Apply / learn more</a>\n\n"
        f"{tag} #Volunteens"
    )


def send_telegram(chat_id: str, text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": False},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def main() -> int:
    posted = load_posted()
    found = find_opportunities(posted)
    fresh = [o for o in found if is_valid(o, posted)][:POSTS_PER_RUN]
    print(f"Found {len(found)}, {len(fresh)} new and valid.")

    if POST_MODE == "review":
        target = os.environ["REVIEW_CHAT_ID"]
    else:
        target = os.environ.get("TELEGRAM_CHAT_ID", "")

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
        save_posted(posted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
