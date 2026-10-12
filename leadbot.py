#!/usr/bin/env python3
"""Round Two lead bot.

Finds local businesses (Google Places API), checks each website for an email
signup gap, pulls a public contact email, drafts a personalized first email,
and appends everything to leads.csv for YOU to review and send.

It never sends anything. Review the 'draft' column, then send it yourself.

Setup:
    pip install requests beautifulsoup4 anthropic
    export GOOGLE_API_KEY=...          # Google Cloud, enable "Places API (New)"
    export ANTHROPIC_API_KEY=...       # optional, for better drafts
    python leadbot.py                  # or: python leadbot.py --max 40
"""
import argparse
import csv
import os
import re
import sys
import time
import urllib.robotparser
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---- EDIT THESE -----------------------------------------------------------
CATEGORIES = ["hair salon", "nail salon", "boutique", "coffee shop", "bakery",
              "yoga studio", "gym", "florist", "pet groomer", "restaurant"]
CITIES = ["Irvine, CA", "Costa Mesa, CA", "Santa Monica, CA", "Pasadena, CA",
          "Long Beach, CA", "Huntington Beach, CA"]
SENDER_NAME = "Dom Owens"
SENDER_BLURB = ("I set up simple email follow up for local businesses. I spent "
                "7+ years running email and automation for Walmart, Cisco, and Hyundai.")
OUTFILE = "leads.csv"
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
UA = "RoundTwoLeadBot/1.0 (+contact: domowensmoore@gmail.com)"
# ---------------------------------------------------------------------------

FIELDS = ["date_found", "business", "category", "city", "address", "phone",
          "website", "rating", "reviews", "contact_email", "has_signup",
          "email_platform", "booking_platform", "gap_note", "score", "draft", "stage"]

PLATFORMS = {
    "mailchimp": ["list-manage.com", "mailchimp.com", "chimpstatic"],
    "klaviyo": ["klaviyo.com", "klaviyo"],
    "constant contact": ["constantcontact.com", "ctctcdn"],
    "squarespace": ["squarespace.com", "sqsp"],
    "shopify": ["cdn.shopify.com", "myshopify"],
    "wix": ["wixstatic.com", "wix.com"],
    "wordpress": ["wp-content"],
}
# Booking tools that usually include built-in email/text marketing
BOOKING = {
    "Mindbody": ["mindbodyonline.com", "clients.mindbodyonline"],
    "Vagaro": ["vagaro.com"],
    "Boulevard": ["joinblvd.com", "blvd.me"],
    "Square Appointments": ["squareup.com/appointments", "book.squareup.com", "square.site"],
    "Acuity": ["acuityscheduling.com", "as.me/"],
    "Fresha": ["fresha.com"],
    "Booksy": ["booksy.com"],
    "GlossGenius": ["glossgenius.com"],
    "Schedulicity": ["schedulicity.com"],
    "Zenoti": ["zenoti.com"],
    "WellnessLiving": ["wellnessliving.com"],
    "Setmore": ["setmore.com"],
}
SIGNUP_WORDS = re.compile(r"(newsletter|subscribe|join (our|the) (list|club)|"
                          r"sign ?up for|email list|get updates|stay in touch)", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
JUNK_EMAIL = re.compile(r"(example\.|sentry|wixpress|squarespace|\.png|\.jpg|noreply|no-reply)", re.I)


def search_places(api_key, query, limit=20):
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": ("places.displayName,places.formattedAddress,"
                             "places.websiteUri,places.nationalPhoneNumber,"
                             "places.rating,places.userRatingCount,places.businessStatus"),
    }
    r = requests.post(PLACES_URL, headers=headers,
                      json={"textQuery": query, "pageSize": limit}, timeout=30)
    r.raise_for_status()
    return r.json().get("places", [])


def allowed_by_robots(url):
    try:
        p = urlparse(url)
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{p.scheme}://{p.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(UA, url)
    except Exception:
        return True  # no readable robots.txt


def fetch(url):
    if not allowed_by_robots(url):
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        if r.ok and "text/html" in r.headers.get("content-type", ""):
            return r.text
    except requests.RequestException:
        pass
    return None


def analyze_html(html):
    """Return (has_signup, platform, emails) for one page of HTML."""
    soup = BeautifulSoup(html, "html.parser")
    low = html.lower()

    platform = next((name for name, keys in PLATFORMS.items()
                     if name in ("mailchimp", "klaviyo", "constant contact")
                     and any(k in low for k in keys)), "")
    has_form = False
    for form in soup.find_all("form"):
        if form.find("input", {"type": "email"}) or "email" in str(form).lower():
            txt = form.get_text(" ", strip=True) + " " + str(form.get("action", ""))
            if SIGNUP_WORDS.search(txt) or platform:
                has_form = True
    if not has_form and platform:
        has_form = True
    if not has_form and SIGNUP_WORDS.search(soup.get_text(" ", strip=True)) \
            and soup.find("input", {"type": "email"}):
        has_form = True

    emails = set()
    for a in soup.select('a[href^="mailto:"]'):
        emails.add(a["href"][7:].split("?")[0].strip())
    emails.update(EMAIL_RE.findall(soup.get_text(" ")))
    emails = {e for e in emails if not JUNK_EMAIL.search(e)}
    booking = next((name for name, keys in BOOKING.items()
                    if any(k in low for k in keys)), "")
    return has_form, platform, sorted(emails), booking


def inspect_site(url):
    """Check homepage plus a contact page. Returns a dict."""
    out = {"has_signup": False, "platform": "", "email": "", "reachable": False,
           "booking": ""}
    html = fetch(url)
    if not html:
        return out
    out["reachable"] = True
    pages = [html]
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if re.search(r"contact|about", a.get_text(" ") + a["href"], re.I):
            sub = urljoin(url, a["href"])
            if urlparse(sub).netloc == urlparse(url).netloc:
                more = fetch(sub)
                if more:
                    pages.append(more)
                break
    emails = []
    for h in pages:
        s, p, e, bk = analyze_html(h)
        out["has_signup"] = out["has_signup"] or s
        out["platform"] = out["platform"] or p
        out["booking"] = out["booking"] or bk
        emails += e
    domain = urlparse(url).netloc.replace("www.", "")
    own = [e for e in emails if domain.split(".")[0] in e.lower()]
    out["email"] = (own or emails or [""])[0]
    return out


def gap_and_score(info, rating, reviews):
    if not info["reachable"]:
        return "site unreachable", 0
    bk = info.get("booking", "")
    if bk and not info["platform"]:
        # Booking tool but no separate email platform: marketing features are
        # often built in but unused. Worth contacting, with a different angle.
        note = (f"uses {bk} for booking; ask whether they use its email tools "
                f"to bring back clients who haven't rebooked")
        score = 50 if not info["has_signup"] else 45
    elif not info["has_signup"]:
        note, score = "no email signup found on the site", 60
    elif info["platform"]:
        note, score = f"has a {info['platform']} signup; ask what happens after someone signs up", 30
    else:
        note, score = "has a signup form; unclear what happens after", 35
    if info["email"]:
        score += 15
    try:
        if float(rating) >= 4.3 and int(reviews) >= 30:
            score += 20  # loved by customers, so repeat business matters
    except (TypeError, ValueError):
        pass
    return note, min(score, 100)


def draft_email(biz, note):
    angle = ""
    if biz.get("booking"):
        angle = (f"They use {biz['booking']} for booking, so do not say they have no email "
                 f"system. Ask whether they use its email tools to bring back clients who "
                 f"haven't rebooked in a while. ")
    prompt = (f"Write a first cold email, under 90 words, plain and human, no dashes, "
              f"no hype. To the owner of {biz['name']} ({biz['category']}, {biz['city']}). "
              f"Specific observation: {note}. {angle}Sender is {SENDER_NAME}: {SENDER_BLURB} "
              f"Ask only for a free 20 minute look at their email setup. "
              f"Include a subject line. Sign off with the sender name and 'Round Two Marketing'.")
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            msg = anthropic.Anthropic().messages.create(
                model=os.getenv("LEADBOT_MODEL", "claude-sonnet-4-5"),
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}])
            return msg.content[0].text.strip()
        except Exception as e:  # fall back to template
            print(f"  (draft fell back to template: {e})", file=sys.stderr)
    if biz.get("booking"):
        return (f"Subject: Bringing back past clients at {biz['name']}\n\n"
                f"Hi,\n\nI saw {biz['name']} uses {biz['booking']} for booking. Are you using "
                f"its email tools to reach clients who haven't rebooked in a while? "
                f"Most owners I talk to only use it for reminders.\n\n"
                f"{SENDER_BLURB} I'd be glad to take a free 20 minute look at your setup and "
                f"show you a simple win-back email. Worth a quick call this week?\n\n"
                f"{SENDER_NAME}\nRound Two Marketing")
    return (f"Subject: Quick question about {biz['name']}'s email list\n\n"
            f"Hi,\n\nI was looking at {biz['name']} and noticed {note}. "
            f"That usually means customers who liked you once never hear from you again.\n\n"
            f"{SENDER_BLURB} I'd be glad to take a free 20 minute look at your setup and "
            f"show you a simple version. Worth a quick call this week?\n\n"
            f"{SENDER_NAME}\nRound Two Marketing")


def load_seen(path):
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {r["website"] for r in csv.DictReader(f)}


def migrate_header(path):
    """If leads.csv was written by an older version, rewrite it with the new columns."""
    if not os.path.exists(path):
        return
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        header = rows and list(rows[0].keys())
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()
        return
    if header != FIELDS:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore", restval="")
            w.writeheader()
            w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=30, help="max new leads this run")
    args = ap.parse_args()

    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        sys.exit("Set GOOGLE_API_KEY first.")

    migrate_header(OUTFILE)
    seen = load_seen(OUTFILE)
    new_file = not os.path.exists(OUTFILE)
    added = 0
    with open(OUTFILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for city in CITIES:
            for cat in CATEGORIES:
                if added >= args.max:
                    break
                try:
                    places = search_places(key, f"{cat} in {city}")
                except requests.RequestException as e:
                    print(f"search failed for {cat} in {city}: {e}", file=sys.stderr)
                    continue
                for p in places:
                    site = p.get("websiteUri", "")
                    if not site or site in seen or p.get("businessStatus") not in (None, "OPERATIONAL"):
                        continue
                    if added >= args.max:
                        break
                    seen.add(site)
                    name = p["displayName"]["text"]
                    print(f"checking {name} ...")
                    info = inspect_site(site)
                    note, score = gap_and_score(info, p.get("rating"), p.get("userRatingCount"))
                    if score < 50 or not info["email"]:
                        continue  # keep only reachable leads worth contacting
                    biz = {"name": name, "category": cat, "city": city,
                           "booking": info.get("booking", "")}
                    w.writerow({
                        "date_found": date.today().isoformat(), "business": name,
                        "category": cat, "city": city,
                        "address": p.get("formattedAddress", ""),
                        "phone": p.get("nationalPhoneNumber", ""), "website": site,
                        "rating": p.get("rating", ""), "reviews": p.get("userRatingCount", ""),
                        "contact_email": info["email"],
                        "has_signup": "yes" if info["has_signup"] else "no",
                        "email_platform": info["platform"],
                        "booking_platform": info.get("booking", ""), "gap_note": note,
                        "score": score, "draft": draft_email(biz, note), "stage": "Found",
                    })
                    f.flush()
                    added += 1
                    time.sleep(1.5)  # be polite
    print(f"Done. {added} new leads added to {OUTFILE}. Review the drafts before sending.")


if __name__ == "__main__":
    main()
