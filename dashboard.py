#!/usr/bin/env python3
"""Builds README.md (shown on your repo's front page) from leads.csv.
Standard library only. Run: python dashboard.py
"""
import csv
import os
from collections import Counter
from datetime import date

STAGES = ["Found", "Contacted", "Replied", "Call booked", "Proposal", "Won", "Lost or later"]
SRC, OUT = "leads.csv", "README.md"


def cell(s, limit=60):
    s = (s or "").replace("|", "/").replace("\n", " ").strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def bars(counter, width=20, top=8):
    items = counter.most_common(top)
    if not items:
        return "_No data yet._\n"
    mx = max(n for _, n in items)
    rows = ["| | | |", "|---|---|---:|"]
    for k, n in items:
        rows.append(f"| {cell(k, 40) or 'Unknown'} | {'█' * max(1, round(n / mx * width))} | {n} |")
    return "\n".join(rows) + "\n"


def main():
    leads = []
    if os.path.exists(SRC):
        with open(SRC, newline="", encoding="utf-8") as f:
            leads = [r for r in csv.DictReader(f) if r.get("business")]

    total = len(leads)
    nosign = sum(1 for l in leads if l.get("has_signup") == "no")
    booking = Counter(l.get("booking_platform") or "None detected" for l in leads)
    scores = [int(l["score"]) for l in leads if (l.get("score") or "").isdigit()]
    avg = round(sum(scores) / len(scores)) if scores else 0
    stages = Counter(l.get("stage") or "Found" for l in leads)
    won = stages.get("Won", 0)

    out = [f"# Round Two Leads\n",
           f"_Updated {date.today().isoformat()} by the lead bot. Leads are in [leads.csv](leads.csv)._\n",
           "## At a glance\n",
           "| Leads found | No email signup | Average score | Clients won |",
           "|---:|---:|---:|---:|",
           f"| {total} | {round(nosign / total * 100) if total else 0}% | {avg} | {won} |\n",
           "## Pipeline\n",
           "| Stage | | Leads |", "|---|---|---:|"]
    mx = max([stages.get(s, 0) for s in STAGES] + [1])
    for s in STAGES:
        n = stages.get(s, 0)
        out.append(f"| {s} | {'█' * round(n / mx * 20) if n else ''} | {n} |")
    out += ["", "## Where the leads are\n", bars(Counter(l.get("city") for l in leads)),
            "## Business types\n", bars(Counter(l.get("category") for l in leads)),
            "## Booking tools found\n", bars(booking),
            "## Call these first\n",
            "Highest scoring leads still in Found or Contacted.\n",
            "| Score | Business | City | Booking tool | Email | Why |", "|---:|---|---|---|---|---|"]
    top = [l for l in leads if (l.get("stage") or "Found") in ("Found", "Contacted")]
    top.sort(key=lambda l: -int(l["score"]) if (l.get("score") or "").isdigit() else 0)
    for l in top[:15]:
        site = l.get("website", "")
        name = f"[{cell(l['business'], 40)}]({site})" if site else cell(l["business"], 40)
        out.append(f"| {l.get('score', '')} | {name} | {cell(l.get('city'), 25)} | "
                   f"{cell(l.get('booking_platform')) or '-'} | {cell(l.get('contact_email'), 35)} | "
                   f"{cell(l.get('gap_note'), 70)} |")
    if not top:
        out.append("| | _No leads to work yet._ | | | | |")
    out += ["", "---",
            "To track progress, open leads.csv, click the pencil icon, and change a lead's `stage` "
            "to Contacted, Replied, Call booked, Proposal, Won, or Lost or later. "
            "This page updates on the next run."]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"Wrote {OUT} with {total} leads.")


if __name__ == "__main__":
    main()
