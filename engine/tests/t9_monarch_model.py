#!/usr/bin/env python3
# T9: the monarch-now model against an INDEPENDENT ORACLE, on synthetic data, with mutants.
#
#   t9_monarch_model.py <engine dir>            # the directory that holds monarchnow/
#       (run it with a python that has engine/requirements.txt installed: one check needs the
#        pinned graphql-core)
#
# EVERYTHING HERE IS INVENTED. The fixture is generated in this file from a fixed seed, and a
# handful of tiny hand-built snapshots pin the edges: the merchants, bills, accounts, tags and
# amounts are made up, and the dates are the clocks the test pins for itself (Oct 5 and Oct 12,
# 2025, and others). Nothing touches the network, the keyring or the cache.
#
# THE ORACLE (below, `Oracle`) recomputes every number and every string on the card from the
# raw fixture with DIFFERENT CODE: Fractions instead of Decimals, plain loops over the raw
# transactions instead of the model's per-month tables, and its own formatting. It imports
# nothing from monarchnow. The hand-pinned strings in `pinned_cases` were typed from arithmetic
# done by hand, so a bug shared by the model and the oracle still has to get past those.
#
# Two rules checked in EVERY scenario besides the oracle:
#   the headline is worded against the DAY ("over a usual day N", N = today), never the month;
#   every chip is one of the rows: the same name, the same figure, the same direction.
#
# What it holds, per scenario: the whole card (hero, chart points, band, labels, note, chips,
# group rows, footer), the face state, the glyph, and the tooltip's lines.
#
# Then every MUTANT (a one-defect copy of the package) must fail at least one check. A mutation
# whose source text no longer matches is an ERROR, not a survivor.
# Exit 1 on any failure, on a surviving mutant, or on a mutation that did not apply.
import datetime
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction as F
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")       # the suite's own pinned zone; the engine uses the system's
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ----------------------------------------------------------------------------------------------
# Fixture
# ----------------------------------------------------------------------------------------------
CATS = {
    "c-groc": ("Groceries", "Food & Dining", "expense"),
    "c-rest": ("Restaurants & Bars", "Food & Dining", "expense"),
    "c-shop": ("Shopping", "Shopping", "expense"),
    "c-gas": ("Gas", "Auto & Transport", "expense"),
    "c-fun": ("Entertainment & Recreation", "Travel & Lifestyle", "expense"),
    "c-med": ("Medical", "Health & Wellness", "expense"),
    "c-kid": ("Child Activities", "Children", "expense"),
    "c-fin": ("Financial & Legal Services", "Financial", "expense"),
    "c-elec": ("Gas & Electric", "Bills & Utilities", "expense"),
    "c-odd": ("Mystery Category", "Other", "expense"),          # not in the group map -> Other
    "c-xfer": ("Transfer", "Transfers", "transfer"),
    "c-ccp": ("Credit Card Payment", "Transfers", "transfer"),
    "c-pay": ("Paychecks", "Income", "income"),
}
GROUP_OF = {"Groceries": "Food", "Restaurants & Bars": "Food", "Shopping": "Shopping", "Gas": "Transport",
            "Entertainment & Recreation": "Fun & travel", "Medical": "Health", "Child Activities": "Kids & education",
            "Financial & Legal Services": "Other", "Gas & Electric": "Home & utilities"}
ORDER = ["Food", "Home & utilities", "Shopping", "Fun & travel", "Transport", "Kids & education", "Health", "Other"]
PALETTE = ["#0072B2", "#56B4E9", "#CC79A7", "#F0E442", "#9085E9", "#D9D9D9", "#7A5FD0", "#9A9A9A"]


def dim(y, m):
    return (datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.date(y, m, 1)).days


def month_iter(first, last):
    y, m = first
    while (y, m) <= last:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


class Rng:
    def __init__(self, seed):
        self.x = seed

    def next(self, lo, hi):
        self.x = (self.x * 1103515245 + 12345) % (1 << 31)
        return lo + self.x % (hi - lo + 1)


def make_snapshot(first, last_day, window_start, fetched_at, seed=1335, history_from=None):
    """Synthetic months from `first` (y, m) through the date `last_day`. With `history_from`, no
    row is dated before it: a household whose Monarch history starts part-way through a month."""
    rng = Rng(seed)
    txns = []

    def add(d, cat, cents, account="a1", tags=(), pending=False, hidden=False):
        if history_from is not None and d < history_from:
            return
        name, group, gtype = CATS[cat]
        txns.append({"id": "t%05d" % len(txns), "date": d.isoformat(), "amount": "%s%d.%02d" % ("-" if cents > 0 else "", abs(cents) // 100, abs(cents) % 100),
                     "category_id": cat, "category": name, "group": group, "group_type": gtype,
                     "tags": sorted(tags), "pending": pending, "hidden": hidden, "account_id": account})

    for (y, m) in month_iter(first, (last_day.year, last_day.month)):
        for day in range(1, dim(y, m) + 1):
            d = datetime.date(y, m, day)
            if d > last_day + datetime.timedelta(days=3):
                break                               # a few FUTURE-dated rows exist on purpose
            r = rng.next(0, 59)
            if r % 3 == 0:
                add(d, "c-groc", rng.next(1800, 11500))
            if r % 4 == 0:
                add(d, "c-rest", rng.next(700, 5200), account="a2")
            if r % 5 == 0:
                add(d, "c-shop", rng.next(2500, 21000))
            if day % 7 == 3:
                add(d, "c-gas", rng.next(3100, 5900))
            if day == 2:
                add(d, "c-fin", 4300, account="a2")
            if day == 16:
                add(d, "c-kid", 5850)
            if day == 20:
                add(d, "c-elec", rng.next(9000, 17500), account="a2")
            if day == 21:
                add(d, "c-fun", 11300)
            if day == 9 and m % 2 == 0:
                add(d, "c-med", rng.next(3000, 19000))
            if day == 11:
                add(d, "c-odd", rng.next(400, 2200))
            if day == dim(y, m):
                add(d, "c-shop", rng.next(3500, 8000))       # spending on the LAST day of every month
            # things that must NOT count
            if day == 1:
                add(d, "c-pay", -310000, account="a2")        # income
            if day == 15:
                add(d, "c-ccp", 185000, account="a2")         # card payment
                add(d, "c-xfer", 75000, account="a2")         # transfer out
            if day == 18:
                add(d, "c-shop", 7700, hidden=True)           # hidden from reports
                add(d, "c-groc", 3400, account="a3")          # an account hidden from reports
            if day == 7:
                add(d, "c-shop", rng.next(30000, 260000), tags=("One-off",))
            # things that must count in a way that is easy to get wrong
            if day == 12:
                add(d, "c-shop", -rng.next(1400, 9000))       # a refund: positive amount, nets off
            if day == 8:
                add(d, "c-med", rng.next(900, 4500), tags=("Work trip",))    # tagged, but not One-off
            if day == 10 or day == 11:
                add(d, "c-rest", rng.next(800, 3500), pending=True)
        if m == 1:
            add(datetime.date(y, 1, 5), "c-fun", 70200)       # a yearly membership
    accounts = [
        {"id": "a1", "name": "Maple Card", "updated_at": iso(fetched_at - 2 * 3600), "hidden": False, "manual": False, "needs_reconnect": False, "sync_disabled": False},
        {"id": "a2", "name": "Birch Checking", "updated_at": iso(fetched_at - 30 * 3600), "hidden": False, "manual": False, "needs_reconnect": True, "sync_disabled": False},
        {"id": "a3", "name": "Hidden Savings", "updated_at": iso(fetched_at - 300 * 3600), "hidden": True, "manual": False, "needs_reconnect": False, "sync_disabled": False},
        {"id": "a4", "name": "Cash Envelope", "updated_at": iso(fetched_at - 900 * 3600), "hidden": False, "manual": True, "needs_reconnect": False, "sync_disabled": False},
        {"id": "a5", "name": "Dormant Store Card", "updated_at": iso(fetched_at - 700 * 3600), "hidden": False, "manual": False, "needs_reconnect": True, "sync_disabled": False},
    ]
    t = last_day
    nxt = datetime.date(t.year + (t.month == 12), t.month % 12 + 1, 2)
    recurring = [
        {"date": t.isoformat(), "amount": "-9.25", "name": "Already Paid Today", "is_past": True, "approximate": False},
        {"date": (t + datetime.timedelta(days=1)).isoformat(), "amount": "910.0", "name": "Example Payroll", "is_past": False, "approximate": False},
        {"date": (t + datetime.timedelta(days=2)).isoformat(), "amount": "-57.0", "name": "Northlight Internet", "is_past": False, "approximate": False},
        {"date": (t + datetime.timedelta(days=2)).isoformat(), "amount": "-7.49", "name": "Songbird Music", "is_past": False, "approximate": False},
        {"date": (t + datetime.timedelta(days=4)).isoformat(), "amount": "-127.37", "name": "Fernhill Insurance", "is_past": False, "approximate": False},
        {"date": (t + datetime.timedelta(days=5)).isoformat(), "amount": "-38.0", "name": "Riverbend Water", "is_past": False, "approximate": True},
        {"date": nxt.isoformat(), "amount": "-57.0", "name": "Northlight Internet", "is_past": False, "approximate": False},
        {"date": (t - datetime.timedelta(days=1)).isoformat(), "amount": "-31.0", "name": "Yesterday Unmarked", "is_past": False, "approximate": False},
    ]
    return {"schema": 1, "source": "fixture", "fetched_at": fetched_at, "window_start": window_start.isoformat(),
            "window_end": last_day.isoformat(), "transactions": txns, "accounts": accounts, "recurring": recurring}


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


def mini(now, txns, window_start, recurring=(), accounts=None):
    """A tiny hand-built snapshot: txns are (date, category id, dollars as a string, extras)."""
    out = []
    for i, row in enumerate(txns):
        d, cat, amount = row[:3]
        extra = row[3] if len(row) > 3 else {}
        name, group, gtype = CATS[cat]
        out.append({"id": "m%03d" % i, "date": d, "amount": amount, "category_id": cat, "category": name, "group": group,
                    "group_type": gtype, "tags": sorted(extra.get("tags", ())), "pending": extra.get("pending", False),
                    "hidden": extra.get("hidden", False), "account_id": extra.get("account", "a1")})
    return {"schema": 1, "source": "mini", "fetched_at": now.timestamp() - 600, "window_start": window_start,
            "window_end": now.date().isoformat(), "transactions": out,
            "accounts": accounts if accounts is not None else [{"id": "a1", "name": "Maple Card", "updated_at": iso(now.timestamp() - 4200), "hidden": False, "manual": False, "needs_reconnect": False, "sync_disabled": False}],
            "recurring": list(recurring)}


# ----------------------------------------------------------------------------------------------
# The oracle. Fractions, plain loops, its own formatting. Imports nothing from monarchnow.
# ----------------------------------------------------------------------------------------------
def o_round(x):
    """Whole dollars, ties away from zero."""
    return (-1 if x < 0 else 1) * int(abs(F(x)) + F(1, 2))


def o_money(n):
    return ("−" if n < 0 else "") + "$" + format(abs(n), ",")


def o_cents(x):
    x = F(x)
    c = (-1 if x < 0 else 1) * int(abs(x) * 100 + F(1, 2))
    sign = "−" if c < 0 else ""
    c = abs(c)
    return "%s$%s" % (sign, format(c // 100, ",")) if c % 100 == 0 else "%s$%s.%02d" % (sign, format(c // 100, ","), c % 100)


def o_arrow(n):
    return ("▲ " if n > 0 else "▼ ") + "$" + format(abs(n), ",")


def o_age(seconds):
    s = max(0, int(seconds))
    if s < 60:
        return "just now"
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 172800:
        return "%dh" % (s // 3600)
    return "%dd" % (s // 86400)


def o_quantile(values, q):
    v = sorted(values)
    pos = F(len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


class Oracle:
    def __init__(self, snap, now, grace=3, one_off="One-off", excluded=(), order=ORDER, group_of=GROUP_OF, since=None):
        self.snap, self.now, self.grace = snap, now, grace
        floor = datetime.date(since[0], since[1], 1) if since else None     # the user's usual_since
        self.one_off, self.order, self.group_of = one_off, list(order), dict(group_of)
        self.today = now.date()
        self.y, self.m, self.d = self.today.year, self.today.month, self.today.day
        self.M = dim(self.y, self.m)
        hidden_accounts = set(a["id"] for a in snap["accounts"] if a["hidden"])
        self.rows = []          # (date, category, spend as Fraction, pending) for spending only
        self.one_offs = []
        for t in snap["transactions"]:
            date = datetime.date.fromisoformat(t["date"])
            if date > self.today:
                continue
            if t["group_type"] != "expense" or t["hidden"] or t["account_id"] in hidden_accounts:
                continue
            if one_off and one_off in t["tags"]:
                if (date.year, date.month) == (self.y, self.m):
                    self.one_offs.append(-F(t["amount"]))
                continue
            if any(tag in t["tags"] for tag in excluded):
                continue
            self.rows.append((date, t["category"], -F(t["amount"]), t["pending"], t["category_id"]))
        # baseline: the complete months before this one, the newest twelve at most. Complete means
        # that neither the fetch window nor the household's own history (its earliest row of ANY
        # kind) starts after the month's first day. Nothing here names a year.
        ws = datetime.date.fromisoformat(snap["window_start"])
        every_date = sorted(datetime.date.fromisoformat(t["date"]) for t in snap["transactions"])
        self.base = []
        y, m = self.y, self.m
        for _ in range(12):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
            day_one = datetime.date(y, m, 1)
            if not every_date or day_one < ws or day_one < every_date[0]:
                break
            if floor is not None and day_one < floor:
                break
            self.base.insert(0, (y, m))

    def spent(self, ym, upto, cat=None):
        return sum((s for (date, c, s, _, _) in self.rows
                    if (date.year, date.month) == ym and date.day <= upto and (cat is None or c == cat)), F(0))

    def cut(self, k, ym):
        return dim(*ym) if k >= self.M else min(k, dim(*ym))

    def usual(self, k, cat=None):
        return sum((self.spent(b, self.cut(k, b), cat) for b in self.base), F(0)) / len(self.base)

    def card(self):
        cur = (self.y, self.m)
        mon = MON[self.m - 1]
        spent = self.spent(cur, self.d)
        hero = {"total_text": o_money(o_round(spent)), "total_label": "spent this month", "pill_text": "%s · day %d of %d" % (mon, self.d, self.M),
                "pace_text": "", "pace_dir": "none", "detail_text": ""}
        state = "none"
        if self.base:
            diff = o_round(spent - self.usual(self.d))
            month_total = self.usual(self.M)
            tol = o_round(max(F(25), month_total / 200))
            if diff > tol:
                state, hero["pace_text"], hero["pace_dir"] = "over", o_arrow(diff) + " over a usual day %d" % self.d, "over"
            elif diff < -tol:
                state, hero["pace_text"], hero["pace_dir"] = "under", o_arrow(diff) + " under a usual day %d" % self.d, "good"
            else:
                state, hero["pace_text"], hero["pace_dir"] = "at", "≈ on a usual month's pace", "good"
            hero["detail_text"] = "on pace for %s · usual %s" % (o_money(o_round(month_total) + diff), o_money(o_round(month_total)))
        else:
            hero["pace_text"] = "no usual month yet (it needs one complete month)"

        cur_series = [F(0)] + [self.spent(cur, k) for k in range(1, self.d + 1)]
        usual_series = [F(0)] + [self.usual(k) for k in range(1, self.M + 1)] if self.base else []
        band = []
        if len(self.base) >= 4:
            for k in range(1, self.M + 1):
                vals = [self.spent(b, self.cut(k, b)) for b in self.base]
                band.append((o_quantile(vals, F(1, 4)), o_quantile(vals, F(3, 4))))
        allv = cur_series + usual_series + [v for p in band for v in p]
        lo, hi = min(allv), max(allv)
        chart = None
        if hi > lo:
            top = hi + (hi - lo) * F(8, 100)
            bottom = lo - (hi - lo) * F(8, 100) if lo < 0 else lo
            ny = lambda v: float((v - bottom) / (top - bottom))
            nx = lambda k: k / self.M
            chart = {"current": [{"x": nx(k), "y": ny(v)} for k, v in enumerate(cur_series)],
                     "usual": [{"x": nx(k), "y": ny(v)} for k, v in enumerate(usual_series)],
                     "band": [{"x": nx(k + 1), "lo": ny(p[0]), "hi": ny(p[1])} for k, p in enumerate(band)],
                     "today_x": nx(self.d), "today_y": ny(cur_series[self.d]), "zero_y": ny(F(0)),
                     "dir": "over" if state == "over" else ("good" if self.base else "none"),
                     "max_text": o_money(o_round(hi)), "min_text": o_money(o_round(lo)) if lo != 0 else "",
                     "span_text": "%s 1–%d" % (mon, self.M),
                     "legend": [{"kind": "line", "text": mon + " so far"}]
                               + ([{"kind": "dash", "text": "usual month " + o_money(o_round(usual_series[self.M]))}] if self.base else [])
                               + ([{"kind": "band", "text": "middle half of months"}] if band else [])}

        parts = []
        if self.one_offs:
            parts.append("%d one-off%s excluded (%s)" % (len(self.one_offs), "" if len(self.one_offs) == 1 else "s", o_money(o_round(sum(self.one_offs)))))
        elif self.one_off:
            parts.append("one-offs excluded")
        pending = sum(1 for (date, _, _, p, _) in self.rows if p and (date.year, date.month) == cur)
        if pending:
            parts.append("%d pending included" % pending)
        if self.base:
            (y1, m1), (y2, m2) = self.base[0], self.base[-1]
            span = ("%s %d" % (MON[m1 - 1], y1) if (y1, m1) == (y2, m2)
                    else "%s–%s %d" % (MON[m1 - 1], MON[m2 - 1], y1) if y1 == y2
                    else "%s %d–%s %d" % (MON[m1 - 1], y1, MON[m2 - 1], y2))
            parts.append("usual = %s average" % span)
            if self.d <= self.grace:                   # the bar's color waits; the card says so
                parts.append("bar color from day %d" % (self.grace + 1))

        months_in_play = set([cur] + self.base)
        cats = sorted(set(c for (date, c, _, _, _) in self.rows if (date.year, date.month) in months_in_play))
        diffs = []
        g_now = dict((g, F(0)) for g in self.order)
        g_usual = dict((g, F(0)) for g in self.order)
        unmapped = set()
        for c in cats:
            now_c = self.spent(cur, self.d, c)
            usual_c = self.usual(self.d, c) if self.base else F(0)
            g = self.group_of.get(c)
            if g is None:
                g = self.order[-1]                     # the last group takes what is not listed
                unmapped.add(c)
            g_now[g] += now_c
            g_usual[g] += usual_c
        if self.base:                                  # the GROUPS furthest from usual, at least $20, three at most
            for i, g in enumerate(self.order):
                dg = o_round(g_now[g] - g_usual[g])
                if abs(dg) >= 20:
                    diffs.append((-abs(dg), i, g, dg))
        chips = [{"label": g, "value_text": o_arrow(dg), "dir": "over" if dg > 0 else "good"} for (_, _, g, dg) in sorted(diffs)[:3]]
        pos = sum(max(v, F(0)) for v in g_now.values())
        swatch = dict((g, PALETTE[i]) for i, g in enumerate(self.order))
        rows = []
        for g in self.order:
            row = {"name": g, "swatch": swatch[g], "value_text": o_money(o_round(g_now[g])), "vs_text": "", "dir": "none",
                   "weight_ratio": float(max(g_now[g], F(0)) / pos) if pos > 0 else 0.0}
            if self.base:
                dg = o_round(g_now[g] - g_usual[g])
                if abs(dg) < 10:
                    row["vs_text"], row["dir"] = "≈", "flat"
                else:
                    row["vs_text"], row["dir"] = o_arrow(dg), ("over" if dg > 0 else "good")
            rows.append(row)

        footer = {"left_text": "", "left_sub_text": "", "right_text": "", "attention_text": ""}
        bills = []
        for r in self.snap["recurring"]:
            rd = datetime.date.fromisoformat(r["date"])
            if rd >= self.today and not r["is_past"] and F(r["amount"]) < 0:
                bills.append((rd, F(r["amount"]), r["name"], r["approximate"]))
        bills.sort()
        end = datetime.date(self.y, self.m, self.M)
        if bills:
            rd, amt, name, approx = bills[0]
            footer["left_text"] = "Next bill: %s · %s %d · %s%s" % (name, MON[rd.month - 1], rd.day, "~" if approx else "", o_cents(-amt))
            rest = [b for b in bills[1:] if b[0] <= end]
            if rest:
                footer["left_sub_text"] = "then %d more by %s %d · %s" % (len(rest), MON[end.month - 1], end.day, o_money(o_round(sum(-b[1] for b in rest))))
        else:
            footer["left_text"] = "No recurring bills in the next 45 days"
        fetched = datetime.datetime.fromtimestamp(self.snap["fetched_at"], TZ)
        since = self.today - datetime.timedelta(days=60)
        active = set(t["account_id"] for t in self.snap["transactions"]
                     if t["group_type"] == "expense" and since <= datetime.date.fromisoformat(t["date"]) <= self.today)
        ages, attention = [], []
        for a in self.snap["accounts"]:
            if a["id"] in active and not a["manual"] and not a["hidden"]:
                if a["needs_reconnect"] or a["sync_disabled"]:
                    attention.append(a["name"])
                if a["updated_at"]:
                    ages.append(((fetched - datetime.datetime.fromisoformat(a["updated_at"])).total_seconds(), a["name"]))
        right = []
        if ages:
            age, name = max(ages)
            word = o_age(age)
            right.append("bank data just synced" if word == "just now" else
                         "bank data %s old (%s)" % (word, name) if age > 86400 else "bank data %s old" % word)
        right.append("checked " + (fetched.strftime("%H:%M") if fetched.date() == self.today else fetched.strftime("%a %H:%M")))
        footer["right_text"] = " · ".join(right)
        if attention:
            footer["attention_text"] = "Reconnect in Monarch: " + ", ".join(sorted(attention))
        card = {"hero": hero, "banner_text": "", "hint_text": "", "chart": chart, "note_text": " · ".join(parts),
                "chips_label": "Furthest", "chips": chips, "groups": rows, "footer": footer, "open_text": "Open Monarch", "unmapped": sorted(unmapped)}
        return card, state

    def doc_state(self):
        age = self.now.timestamp() - self.snap["fetched_at"]
        _, state = self.card()
        if age > 36 * 3600:
            return "broken"
        if age > 3 * 3600:
            return "stale"
        if self.d <= self.grace:
            return "plain"                              # days 1..grace: a neutral butterfly
        return {"over": "over", "under": "good", "at": "good"}.get(state, "plain")


# ----------------------------------------------------------------------------------------------
# Comparison
# ----------------------------------------------------------------------------------------------
def diff(a, b, path=""):
    """Differences between two JSON-ish values; floats within 0.00015 (four-decimal rounding)."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append("%s.%s: only on one side" % (path, k))
            else:
                out += diff(a[k], b[k], path + "." + k)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append("%s: length %d vs %d" % (path, len(a), len(b)))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff(x, y, "%s[%d]" % (path, i))
    elif isinstance(a, float) or isinstance(b, float):
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= 0.00015):
            out.append("%s: %r vs %r" % (path, a, b))
    elif a != b:
        out.append("%s: %r vs %r" % (path, a, b))
    return out


def at(y, mo, d, h=14, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=TZ)


def scenarios():
    """(name, snapshot, now)"""
    out = []
    now = at(2025, 10, 12)
    full = make_snapshot((2025, 1), now.date(), datetime.date(2025, 1, 1), now.timestamp() - 1800)
    out.append(("main: Oct 12, nine baseline months", full, now))
    for day in (1, 2, 3, 4, 31):
        n = at(2025, 10, day, 8)
        out.append(("Oct %d" % day, make_snapshot((2025, 1), n.date(), datetime.date(2025, 1, 1), n.timestamp() - 600), n))
    for day in (29, 30):
        n = at(2025, 9, day)
        out.append(("Sep %d: a 30-day month against 31-day baseline months" % day,
                    make_snapshot((2025, 1), n.date(), datetime.date(2025, 1, 1), n.timestamp() - 600), n))
    for day in (10, 28):
        n = at(2025, 2, day)
        out.append(("Feb %d: 28 days, January alone as the baseline" % day,
                    make_snapshot((2025, 1), n.date(), datetime.date(2025, 1, 1), n.timestamp() - 600), n))
    out.append(("history reaches back to Nov 2024: the usual month takes all eleven months",
                make_snapshot((2024, 11), now.date(), datetime.date(2024, 11, 1), now.timestamp() - 1800), now))
    out.append(("history starts Mar 10 inside a longer window: March is not a complete month",
                make_snapshot((2025, 3), now.date(), datetime.date(2024, 9, 1), now.timestamp() - 1800,
                              history_from=datetime.date(2025, 3, 10)), now))
    out.append(("history starts on Mar 1 inside a longer window: March counts",
                make_snapshot((2025, 3), now.date(), datetime.date(2024, 9, 1), now.timestamp() - 1800), now))
    n = at(2026, 3, 15)
    out.append(("Mar 2026: twelve months at most", make_snapshot((2025, 1), n.date(), datetime.date(2025, 1, 1), n.timestamp() - 600), n))
    out.append(("window starts Mar 10: March is not a complete month",
                make_snapshot((2025, 3), now.date(), datetime.date(2025, 3, 10), now.timestamp() - 1800), now))
    out.append(("window starts Jul 15: two baseline months, no band",
                make_snapshot((2025, 7), now.date(), datetime.date(2025, 7, 15), now.timestamp() - 1800), now))
    n = at(2025, 1, 20)
    out.append(("Jan 20: no complete month yet", make_snapshot((2025, 1), n.date(), datetime.date(2025, 1, 1), n.timestamp() - 600), n))
    stale = dict(full, fetched_at=now.timestamp() - 4 * 3600)
    out.append(("data four hours old: stale", stale, now))
    return out


def pinned_cases():
    """Tiny snapshots with the expected strings typed by hand. (name, snapshot, now, {path: value})"""
    now = at(2025, 10, 5)
    ws = "2025-09-01"
    base = [("2025-09-01", "c-groc", "-1000.00")]
    out = []
    # usual by Oct 5 = 1000, usual month = 1000, tolerance = max(25, 5) = 25
    out.append(("exactly at the tolerance is still 'at'", mini(now, base + [("2025-10-02", "c-groc", "-1025.00")], ws), now,
                {"hero.total_text": "$1,025", "hero.pace_text": "≈ on a usual month's pace", "hero.pace_dir": "good",
                 "hero.detail_text": "on pace for $1,025 · usual $1,000", "face": "good"}))
    out.append(("one dollar past the tolerance is over", mini(now, base + [("2025-10-02", "c-groc", "-1026.00")], ws), now,
                {"hero.pace_text": "▲ $26 over a usual day 5", "hero.pace_dir": "over",
                 "hero.detail_text": "on pace for $1,026 · usual $1,000", "face": "over", "chart.dir": "over"}))
    out.append(("one dollar past the tolerance the other way is under", mini(now, base + [("2025-10-02", "c-groc", "-974.00")], ws), now,
                {"hero.pace_text": "▼ $26 under a usual day 5", "hero.pace_dir": "good",
                 "hero.detail_text": "on pace for $974 · usual $1,000", "face": "good", "chart.dir": "good"}))
    out.append(("a rounded figure decides, not the cents: 1025.49 shows $25 and is 'at'", mini(now, base + [("2025-10-02", "c-groc", "-1025.49")], ws), now,
                {"hero.total_text": "$1,025", "hero.pace_text": "≈ on a usual month's pace", "face": "good"}))
    out.append(("1025.50 shows $26 and is over", mini(now, base + [("2025-10-02", "c-groc", "-1025.50")], ws), now,
                {"hero.total_text": "$1,026", "hero.pace_text": "▲ $26 over a usual day 5", "face": "over"}))
    out.append(("a tie rounds up, not to even: 1024.50 shows $1,025", mini(now, base + [("2025-10-02", "c-groc", "-1024.50")], ws), now,
                {"hero.total_text": "$1,025", "groups[0].value_text": "$1,025", "groups[0].vs_text": "\u25b2 $25"}))
    # group rows: Food usual 1000. Kids & education: usual 0, now 10 -> shown; Transport 9 -> "≈"
    out.append(("a group $10 from usual is shown, $9 is '≈'", mini(now, base + [("2025-10-02", "c-groc", "-1000.00"), ("2025-10-03", "c-kid", "-10.00"), ("2025-10-03", "c-gas", "-9.00")], ws), now,
                {"groups[5].vs_text": "▲ $10", "groups[5].dir": "over", "groups[4].vs_text": "≈", "groups[4].dir": "flat",
                 "groups[0].vs_text": "≈", "groups[0].value_text": "$1,000", "chips": []}))
    # chips: the GROUPS, by magnitude, at least $20, three at most; the biggest is an UNDER.
    # By Oct 5 against September: Food 200 + 20 - 500 = -280 (groceries AND restaurants, one chip),
    # Kids & education 0 - 60 = -60, Shopping 450 - 400 = +50, Transport 130 - 100 = +30 (fourth: left out),
    # Health 0 - 19 = -19 (under the $20 floor). Category names never appear.
    sep = [("2025-09-01", "c-groc", "-500.00"), ("2025-09-02", "c-shop", "-400.00"), ("2025-09-03", "c-gas", "-100.00"),
           ("2025-09-03", "c-kid", "-60.00"), ("2025-09-03", "c-med", "-19.00")]
    octo = [("2025-10-01", "c-groc", "-200.00"), ("2025-10-02", "c-shop", "-450.00"), ("2025-10-02", "c-gas", "-130.00"),
            ("2025-10-03", "c-rest", "-20.00")]
    out.append(("chips are the rows: group names, magnitude order, largest an under, a fourth left out, $19 none", mini(now, sep + octo, ws), now,
                {"chips[0].label": "Food", "chips[0].value_text": "▼ $280", "chips[0].dir": "good",
                 "chips[1].label": "Kids & education", "chips[1].value_text": "▼ $60", "chips[1].dir": "good",
                 "chips[2].label": "Shopping", "chips[2].value_text": "▲ $50", "chips[2].dir": "over",
                 "chips.length": 3, "hero.total_text": "$800",
                 "groups[0].vs_text": "▼ $280", "groups[4].vs_text": "▲ $30", "groups[6].vs_text": "▼ $19"}))
    # refunds only: a negative month so far
    out.append(("refunds can take the month below zero", mini(now, base + [("2025-10-02", "c-shop", "45.00")], ws), now,
                {"hero.total_text": "−$45", "chart.min_text": "−$45", "groups[2].value_text": "−$45", "groups[2].weight_ratio": 0.0}))
    # by hand: Food 300, Transport 100, Shopping -100 (a refund). The bar is shares of the POSITIVE
    # groups: 300/400 and 100/400, nothing for the group below zero. The month's total nets: $300.
    out.append(("the segmented bar is shares of the positive groups only", mini(now, base + [("2025-10-02", "c-groc", "-300.00"), ("2025-10-03", "c-shop", "100.00"), ("2025-10-03", "c-gas", "-100.00")], ws), now,
                {"hero.total_text": "$300", "groups[0].weight_ratio": 0.75, "groups[4].weight_ratio": 0.25, "groups[2].weight_ratio": 0.0, "groups[2].value_text": "\u2212$100"}))
    # what never counts
    never = [("2025-10-02", "c-xfer", "-750.00"), ("2025-10-02", "c-ccp", "-1850.00"), ("2025-10-02", "c-pay", "3100.00"),
             ("2025-10-02", "c-shop", "-777.00", {"tags": ("One-off",)}), ("2025-10-02", "c-shop", "-99.00", {"hidden": True}),
             ("2025-10-09", "c-shop", "-88.00"), ("2025-10-03", "c-rest", "-12.00", {"pending": True})]
    out.append(("transfers, card payments, income, One-off, hidden and future rows never count", mini(now, base + never, ws), now,
                {"hero.total_text": "$12", "note_text": "1 one-off excluded ($777) · 1 pending included · usual = Sep 2025 average"}))
    # bills
    # by hand: after Songbird Music, two more fall on or before Oct 31: 38 + 127.37 = 165.37 -> $165
    rec = [{"date": "2025-10-05", "amount": "-9.25", "name": "Paid Already", "is_past": True, "approximate": False},
           {"date": "2025-10-06", "amount": "910.0", "name": "Example Payroll", "is_past": False, "approximate": False},
           {"date": "2025-10-08", "amount": "-7.49", "name": "Songbird Music", "is_past": False, "approximate": False},
           {"date": "2025-10-10", "amount": "-38.0", "name": "Riverbend Water", "is_past": False, "approximate": True},
           {"date": "2025-10-16", "amount": "-127.37", "name": "Fernhill Insurance", "is_past": False, "approximate": False},
           {"date": "2025-11-02", "amount": "-57.0", "name": "Northlight Internet", "is_past": False, "approximate": False}]
    out.append(("next bill: not the past one, not income; 'then' stops at month end", mini(now, base, ws, recurring=rec), now,
                {"footer.left_text": "Next bill: Songbird Music · Oct 8 · $7.49", "footer.left_sub_text": "then 2 more by Oct 31 · $165"}))
    out.append(("an approximate next bill says so", mini(now, base, ws, recurring=rec[3:]), now,
                {"footer.left_text": "Next bill: Riverbend Water · Oct 10 · ~$38"}))
    out.append(("no bills in the window", mini(now, base, ws, recurring=rec[:2]), now,
                {"footer.left_text": "No recurring bills in the next 45 days", "footer.left_sub_text": ""}))
    # the two clocks: the OLDEST active account speaks for the banks
    accts = [{"id": "a1", "name": "Maple Card", "updated_at": iso(now.timestamp() - 600 - 2 * 3600), "hidden": False, "manual": False, "needs_reconnect": False, "sync_disabled": False},
             {"id": "a2", "name": "Birch Checking", "updated_at": iso(now.timestamp() - 600 - 50 * 3600), "hidden": False, "manual": False, "needs_reconnect": False, "sync_disabled": False}]
    both = base + [("2025-10-02", "c-groc", "-10.00", {"account": "a2"})]
    out.append(("bank data age is the oldest active account's", mini(now, both, ws, accounts=accts), now,
                {"footer.right_text": "bank data 2d old (Birch Checking) · checked 13:50"}))
    out.append(("an account with no recent spending does not speak", mini(now, base, ws, accounts=accts), now,
                {"footer.right_text": "bank data 2h old · checked 13:50"}))
    return out


def wording_rules(doc, now):
    """The headline is against the DAY; the chips ARE rows. Returns the list of breaches."""
    out = []
    card = doc["card"]
    hero = card["hero"]
    text = hero["pace_text"]
    if text[:1] in ("\u25b2", "\u25bc"):
        word = "over" if text[:1] == "\u25b2" else "under"
        m = re.fullmatch(r"[\u25b2\u25bc] \$[0-9,]+ (over|under) a usual day ([0-9]+)", text)
        if not m or m.group(1) != word or int(m.group(2)) != now.day:
            out.append("the headline must read '%s a usual day %d': %r" % (word, now.day, text))
        if "month" in text:
            out.append("the headline must not be worded against the month: %r" % text)
        if not re.fullmatch(r"on pace for \u2212?\$[0-9,]+ \u00b7 usual \$[0-9,]+", hero["detail_text"]):
            out.append("'on pace for $X \u00b7 usual $Y' must stay as it is: %r" % hero["detail_text"])
    rows = dict((g["name"], g) for g in card["groups"])
    labels = [c["label"] for c in card["chips"]]
    if len(set(labels)) != len(labels) or len(labels) > 3:
        out.append("chips must be at most three, each a different row: %r" % labels)
    for c in card["chips"]:
        row = rows.get(c["label"])
        if set(c) != {"label", "value_text", "dir"}:
            out.append("a chip carries unexpected fields: %r" % sorted(c))
        if row is None:
            out.append("chip %r is not the name of a row" % c["label"])
        elif (row["vs_text"], row["dir"]) != (c["value_text"], c["dir"]) or c["dir"] not in ("over", "good"):
            out.append("chip %r says %r/%r, its row says %r/%r" % (c["label"], c["value_text"], c["dir"], row["vs_text"], row["dir"]))
    if "colour" in json.dumps(doc, ensure_ascii=False).lower():
        out.append("'colour' in user-facing text (US spelling: color)")
    return out


def dig(doc, path):
    cur = doc
    for part in path.replace("]", "").replace("[", ".").split("."):
        if part == "length":
            return len(cur)
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def check(engine_dir):
    """Run every check against the package in engine_dir. Returns the list of failures."""
    sys.path.insert(0, engine_dir)
    for name in [n for n in sys.modules if n == "monarchnow" or n.startswith("monarchnow.")]:
        del sys.modules[name]
    model = importlib.import_module("monarchnow.model")
    adapter = importlib.import_module("monarchnow.adapter")
    fails = []

    def need(ok, what):
        if not ok:
            fails.append(what)

    need(ord(model.GLYPH) == 0xF1589 and ord(model.BROKEN_GLYPH) == 0xF0338 and model.GLYPH != model.BROKEN_GLYPH, "glyphs")

    for name, snap, now in scenarios():
        oracle = Oracle(snap, now)
        want_card, _ = oracle.card()
        doc = model.build_doc(snap, now, "")
        got = json.loads(json.dumps(doc))          # also proves it serialises
        want_state = oracle.doc_state()
        need(got["face"]["state"] == want_state, "%s: face %r, oracle %r" % (name, got["face"]["state"], want_state))
        got_card = dict(got["card"])
        if want_state == "stale":
            need(got_card["banner_text"].startswith("Data is 4h old"), "%s: stale banner %r" % (name, got_card["banner_text"]))
            got_card["banner_text"] = ""
        for d in diff(got_card, want_card, "card")[:6]:
            fails.append("%s: %s" % (name, d))
        need(got["text"] == model.GLYPH, "%s: a live or stale document must carry the butterfly" % name)
        fails.extend("%s: %s" % (name, f) for f in wording_rules(got, now))
        tip = got["tooltip"].split("\n")
        need(got["card"]["hero"]["total_text"] + " spent this month" in tip, "%s: tooltip lacks the total" % name)
        need(all(any(line.startswith(g["name"]) and g["value_text"] in line for line in tip) for g in got["card"]["groups"]), "%s: tooltip lacks a group row" % name)
        need(got["card"]["footer"]["right_text"] in tip, "%s: tooltip lacks the clocks" % name)
        if got["card"]["chart"]:
            c = got["card"]["chart"]
            pts = c["current"] + c["usual"] + [{"x": b["x"], "y": b["lo"]} for b in c["band"]] + [{"x": b["x"], "y": b["hi"]} for b in c["band"]]
            need(all(0.0 <= p["x"] <= 1.0 and 0.0 <= p["y"] <= 1.0 for p in pts), "%s: a chart point is outside the frame" % name)
            need(c["current"][-1]["x"] == c["today_x"] and c["current"][-1]["y"] == c["today_y"], "%s: today is not the current line's last point" % name)
        need(abs(sum(g["weight_ratio"] for g in got["card"]["groups"]) - 1.0) < 0.001 or all(g["weight_ratio"] == 0 for g in got["card"]["groups"]), "%s: weights do not sum to 1" % name)

    # the baseline the oracle expects, said out loud for three of the scenarios
    named = dict((n, (s, w)) for n, s, w in scenarios())
    s, w = named["history reaches back to Nov 2024: the usual month takes all eleven months"]
    need(model.baseline_months(s, w.date()) == [(2024, 11), (2024, 12)] + [(2025, k) for k in range(1, 10)], "the baseline must be the history's own complete months, whatever the year")
    s, w = named["history starts Mar 10 inside a longer window: March is not a complete month"]
    need(model.baseline_months(s, w.date()) == [(2025, k) for k in range(4, 10)], "the month the history starts part-way must not be in the baseline")
    s, w = named["history starts on Mar 1 inside a longer window: March counts"]
    need(model.baseline_months(s, w.date()) == [(2025, k) for k in range(3, 10)], "a month whose first day has a transaction is complete")
    need(model.baseline_months(dict(s, transactions=[]), w.date()) == [], "no transactions, no baseline")
    main_doc = model.build_doc(named["main: Oct 12, nine baseline months"][0], named["main: Oct 12, nine baseline months"][1], "")
    need("usual = Jan–Sep 2025 average" in main_doc["card"]["note_text"], "the note must name the months the usual month was computed from: %r" % main_doc["card"]["note_text"])
    s, w = named["Mar 2026: twelve months at most"]
    need(model.baseline_months(s, w.date()) == [(2025, k) for k in range(3, 13)] + [(2026, 1), (2026, 2)], "baseline must be the newest twelve months")
    s, w = named["window starts Mar 10: March is not a complete month"]
    need(model.baseline_months(s, w.date()) == [(2025, k) for k in range(4, 10)], "an incomplete month must not be in the baseline")
    s, w = named["Jan 20: no complete month yet"]
    d0 = model.build_doc(s, w, "")
    need(d0["face"]["state"] == "plain" and d0["card"]["hero"]["pace_dir"] == "none" and d0["card"]["chips"] == [], "no baseline must be plain, untinted")

    for name, snap, now, expect in pinned_cases():
        doc = json.loads(json.dumps(model.build_doc(snap, now, "")))
        fails.extend("pinned/%s: %s" % (name, f) for f in wording_rules(doc, now))
        oracle_card, _ = Oracle(snap, now).card()
        for d in diff(doc["card"], oracle_card, "card")[:4]:
            fails.append("pinned/%s (oracle): %s" % (name, d))
        for path, value in expect.items():
            try:
                got = doc["face"]["state"] if path == "face" else dig(doc["card"], path)
            except (KeyError, IndexError, TypeError):
                got = "<missing>"
            need(got == value, "pinned/%s: %s is %r, expected %r" % (name, path, got, value))

    # age: stale keeps the butterfly and says so; too old is BROKEN and never a butterfly
    name, snap, now = scenarios()[0]
    old = dict(snap, fetched_at=now.timestamp() - 40 * 3600)
    doc = model.build_doc(old, now, "Monarch could not be reached")
    need(doc["face"]["state"] == "broken" and doc["text"] == model.BROKEN_GLYPH and doc["card"]["hero"] is None, "data older than 36 h must be the broken document")
    need("1d old" in doc["card"]["banner_text"] or "40h old" in doc["card"]["banner_text"], "the broken banner must say how old")
    need("could not be reached" in doc["card"]["banner_text"], "the broken banner must say why")
    for hours, state in ((2.9, None), (3.1, "stale"), (35.9, "stale"), (36.1, "broken")):
        doc = model.build_doc(dict(snap, fetched_at=now.timestamp() - hours * 3600), now, "")
        need((doc["face"]["state"] == state) if state else doc["face"]["state"] in ("good", "over"), "age %.1f h: face %r" % (hours, doc["face"]["state"]))
        need((doc["text"] == model.BROKEN_GLYPH) == (doc["face"]["state"] == "broken"), "age %.1f h: glyph and state disagree" % hours)
    doc = model.build_doc(dict(snap, fetched_at=now.timestamp() - 2 * 3600), now, "Monarch could not be reached")
    need(doc["face"]["state"] in ("good", "over") and "Last check failed (Monarch could not be reached)" in doc["card"]["banner_text"], "a failed check behind fresh data must be said on the card")
    b = model.broken_doc("Not signed in", "Sign in: run `monarch-now login` in a terminal.")
    need(b["text"] == model.BROKEN_GLYPH and b["face"]["state"] == "broken" and "Not signed in" in b["tooltip"] and "monarch-now login" in b["tooltip"], "broken_doc")

    # THE GRACE PERIOD: on days 1..N the face is plain whatever the pace; the card keeps its
    # numbers and its own tint and says from which day the bar takes a color. Usual by any of
    # these days = 1000 (one September charge on the 1st); 1100 is over by 100, 10 is under by 990.
    for grace, day, amount, want_face, want_dir, want_pace in (
            (3, 1, "-1100.00", "plain", "over", "OVERDAY"),
            (3, 2, "-1100.00", "plain", "over", "OVERDAY"),
            (3, 3, "-1100.00", "plain", "over", "OVERDAY"),
            (3, 3, "-10.00", "plain", "good", "UNDERDAY"),
            (3, 4, "-1100.00", "over", "over", "OVERDAY"),
            (3, 4, "-10.00", "good", "good", "UNDERDAY"),
            (0, 1, "-1100.00", "over", "over", "OVERDAY"),
            (0, 1, "-10.00", "good", "good", "UNDERDAY"),
            (5, 5, "-1100.00", "plain", "over", "OVERDAY"),
            (5, 6, "-1100.00", "over", "over", "OVERDAY"),
            (31, 31, "-1100.00", "plain", "over", "OVERDAY")):
        now_g = at(2025, 10, day)
        snap_g = mini(now_g, [("2025-09-01", "c-groc", "-1000.00"), ("2025-10-01", "c-groc", amount)], "2025-09-01")
        doc = json.loads(json.dumps(model.build_doc(snap_g, now_g, "", grace)))
        tag = "grace %d, day %d, %s: " % (grace, day, amount)
        need(doc["face"]["state"] == want_face, tag + "face %r, expected %r" % (doc["face"]["state"], want_face))
        need(doc["text"] == model.GLYPH, tag + "the butterfly must stay a butterfly")
        want_pace = ("\u25b2 $100 over a usual day %d" if want_pace == "OVERDAY" else "\u25bc $990 under a usual day %d") % day
        need(doc["card"]["hero"]["pace_dir"] == want_dir and doc["card"]["hero"]["pace_text"] == want_pace and doc["card"]["chart"]["dir"] == want_dir,
             tag + "the card must keep its numbers and its tint: %r %r" % (doc["card"]["hero"]["pace_dir"], doc["card"]["hero"]["pace_text"]))
        said = ("bar color from day %d" % (grace + 1)) in doc["card"]["note_text"]
        need(said == (day <= grace), tag + "the note must say when the color starts, and only then: %r" % doc["card"]["note_text"])
        want_card, _ = Oracle(snap_g, now_g, grace).card()
        for d_ in diff(doc["card"], want_card, "card")[:3]:
            fails.append(tag + "(oracle) " + d_)
        need(Oracle(snap_g, now_g, grace).doc_state() == doc["face"]["state"], tag + "oracle face")
    # grace never hides age: stale and broken on day 1 are still stale and broken
    now_g = at(2025, 10, 1)
    snap_g = mini(now_g, [("2025-09-01", "c-groc", "-1000.00"), ("2025-10-01", "c-groc", "-1100.00")], "2025-09-01")
    need(model.build_doc(dict(snap_g, fetched_at=now_g.timestamp() - 4 * 3600), now_g, "")["face"]["state"] == "stale", "grace must not hide stale data")
    need(model.build_doc(dict(snap_g, fetched_at=now_g.timestamp() - 40 * 3600), now_g, "")["face"]["state"] == "broken", "grace must not hide dead data")
    need(model.PACE_GRACE_DAYS == 3, "the default grace is three days")

    # nothing on the card may call for red, and the only directions are the ones the painter knows
    doc = model.build_doc(scenarios()[0][1], scenarios()[0][2], "")
    dirs = [doc["card"]["hero"]["pace_dir"], doc["card"]["chart"]["dir"]] + [c["dir"] for c in doc["card"]["chips"]] + [g["dir"] for g in doc["card"]["groups"]]
    need(set(dirs) <= {"good", "over", "flat", "none"}, "an unknown direction: %r" % sorted(set(dirs)))
    need(doc["card"]["unmapped"] == ["Mystery Category"], "an unmapped category must be reported")

    # THE CONFIGURABLE PARTS: the one-off tag, more excluded tags, the groups. Each is held against
    # the oracle given the same setting; a few strings are pinned by hand.
    groups_mod = importlib.import_module("monarchnow.groups")
    name, snap, now = scenarios()[0]
    default_doc = json.loads(json.dumps(model.build_doc(snap, now, "")))

    def with_settings(label, settings, **oracle_kw):
        doc = json.loads(json.dumps(model.build_doc(snap, now, "", 3, settings)))
        want_card, _ = Oracle(snap, now, **oracle_kw).card()
        for d_ in diff(doc["card"], want_card, "card")[:4]:
            fails.append("settings/%s (oracle): %s" % (label, d_))
        fails.extend("settings/%s: %s" % (label, f) for f in wording_rules(doc, now))
        return doc

    d1 = with_settings("an excluded tag", model.Settings(excluded_tags=("Work trip",)), excluded=("Work trip",))
    need(d1["card"]["hero"]["total_text"] != default_doc["card"]["hero"]["total_text"], "an excluded tag must take its transactions out of the month")
    need(d1["card"]["note_text"] == default_doc["card"]["note_text"], "an excluded tag is left out without a word in the note")
    d2 = with_settings("a renamed one-off tag", model.Settings(one_off_tag="Big buy"), one_off="Big buy")
    need(d2["card"]["note_text"].startswith("one-offs excluded · "), "a renamed one-off tag: the old tag's rows are ordinary spending now: %r" % d2["card"]["note_text"])
    d3 = with_settings("no one-off tag at all", model.Settings(one_off_tag=""), one_off="")
    need("one-off" not in d3["card"]["note_text"], "without a one-off tag the note must not claim one-offs are excluded: %r" % d3["card"]["note_text"])
    need(d2["card"]["hero"]["total_text"] == d3["card"]["hero"]["total_text"] != default_doc["card"]["hero"]["total_text"], "a tag nobody excludes must count")
    own_order = ["Eating", "Wheels", "Everything else"]
    own_map = {"Groceries": "Eating", "Restaurants & Bars": "Eating", "Gas": "Wheels"}
    d4 = with_settings("three groups of one's own", model.Settings(grouping=groups_mod.Grouping(own_order, own_map)), order=own_order, group_of=own_map)
    need([g["name"] for g in d4["card"]["groups"]] == own_order and [g["swatch"] for g in d4["card"]["groups"]] == PALETTE[:3], "configured groups must be the rows, in order, with the first swatches")
    need("Shopping" in d4["card"]["unmapped"] and "Groceries" not in d4["card"]["unmapped"], "what the configured groups do not list is reported: %r" % d4["card"]["unmapped"])
    need(d4["card"]["hero"] == default_doc["card"]["hero"], "regrouping must not change the month's total or pace")
    # by hand: Sep 1 groceries 1000 is the usual month. Oct 2: groceries 300, a 200 purchase tagged
    # "Big buy", a 50 medical bill tagged "Work trip". With both tags configured only the 300
    # counts and the note counts one one-off; with the defaults all three count: 550.
    now_s = at(2025, 10, 5)
    snap_s = mini(now_s, [("2025-09-01", "c-groc", "-1000.00"), ("2025-10-02", "c-groc", "-300.00"),
                          ("2025-10-02", "c-shop", "-200.00", {"tags": ("Big buy",)}), ("2025-10-02", "c-med", "-50.00", {"tags": ("Work trip",)})], "2025-09-01")
    doc = model.build_doc(snap_s, now_s, "", 3, model.Settings(one_off_tag="Big buy", excluded_tags=("Work trip",)))
    need(doc["card"]["hero"]["total_text"] == "$300" and doc["card"]["note_text"] == "1 one-off excluded ($200) · usual = Sep 2025 average",
         "configured tags, by hand: %r / %r" % (doc["card"]["hero"]["total_text"], doc["card"]["note_text"]))
    doc = model.build_doc(snap_s, now_s, "")
    need(doc["card"]["hero"]["total_text"] == "$550" and doc["card"]["note_text"] == "one-offs excluded · usual = Sep 2025 average",
         "default tags, by hand: %r / %r" % (doc["card"]["hero"]["total_text"], doc["card"]["note_text"]))
    # usual_since: the user's own floor under the usual month. Never a figure, only where it may start.
    d5 = with_settings("usual_since inside the history", model.Settings(usual_since=(2025, 4)), since=(2025, 4))
    need(model.baseline_months(snap, now.date(), since=(2025, 4)) == [(2025, k) for k in range(4, 10)], "usual_since must be the first month of the baseline")
    need("usual = Apr–Sep 2025 average" in d5["card"]["note_text"], "the note must name the months actually used: %r" % d5["card"]["note_text"])
    need(d5["card"]["hero"]["total_text"] == default_doc["card"]["hero"]["total_text"] and d5["card"]["hero"]["detail_text"] != default_doc["card"]["hero"]["detail_text"],
         "usual_since changes the usual month, never this month's spending")
    d6 = with_settings("usual_since older than the history", model.Settings(usual_since=(2024, 1)), since=(2024, 1))
    need(d6["card"] == default_doc["card"], "a floor older than the history must change nothing")
    d7 = with_settings("usual_since this very month", model.Settings(usual_since=(2025, 10)), since=(2025, 10))
    need(d7["face"]["state"] == "plain" and d7["card"]["hero"]["pace_text"].startswith("no usual month yet") and d7["card"]["chips"] == [],
         "a floor that leaves no complete month means no usual month: %r" % d7["card"]["hero"]["pace_text"])
    s, w = named["Mar 2026: twelve months at most"]
    need(model.baseline_months(s, w.date(), since=(2025, 6)) == [(2025, k) for k in range(6, 13)] + [(2026, 1), (2026, 2)], "usual_since inside the twelve months shortens them")
    need(model.baseline_months(s, w.date(), since=(2025, 1)) == [(2025, k) for k in range(3, 13)] + [(2026, 1), (2026, 2)], "usual_since outside the twelve months leaves the cap in force")
    # by hand: August 400 and September 1000, both on the 1st; October so far 700. The usual month
    # is (400 + 1000) / 2 = 700: on pace. With usual_since = September it is 1000: 300 under.
    snap_u = mini(now_s, [("2025-08-01", "c-groc", "-400.00"), ("2025-09-01", "c-groc", "-1000.00"), ("2025-10-02", "c-groc", "-700.00")], "2025-08-01")
    doc = model.build_doc(snap_u, now_s, "")
    need((doc["card"]["hero"]["pace_text"], doc["card"]["hero"]["detail_text"], doc["card"]["note_text"]) ==
         ("≈ on a usual month's pace", "on pace for $700 · usual $700", "one-offs excluded · usual = Aug–Sep 2025 average"),
         "no usual_since, by hand: %r" % (doc["card"]["hero"],))
    doc = model.build_doc(snap_u, now_s, "", 3, model.Settings(usual_since=(2025, 9)))
    need((doc["card"]["hero"]["pace_text"], doc["card"]["hero"]["detail_text"], doc["card"]["note_text"], doc["face"]["state"]) ==
         ("▼ $300 under a usual day 5", "on pace for $700 · usual $1,000", "one-offs excluded · usual = Sep 2025 average", "good"),
         "usual_since = September, by hand: %r" % (doc["card"]["hero"],))
    need(groups_mod.Grouping().group_of("Software & Subscriptions") == ("Home & utilities", True), "a stock Monarch category must not be unmapped by default")
    # the defaults are the eight groups this file's oracle knows, and the config's [[groups]] rules
    need(groups_mod.Grouping().order == ORDER and all(groups_mod.CATEGORY_GROUP.get(c) == g for c, g in GROUP_OF.items()), "the default groups")
    g, why = groups_mod.from_config([{"name": "Eating", "categories": ["Groceries"]}, {"name": " Rest "}])
    need(g is not None and why == "" and g.order == ["Eating", "Rest"] and g.group_of("Groceries") == ("Eating", True) and g.group_of("Anything") == ("Rest", False),
         "a good [[groups]] list must be taken as written, the last group catching the rest")
    for label, bad in (("nine groups", [{"name": "G%d" % i} for i in range(9)]),
                       ("a name twice", [{"name": "A"}, {"name": "A"}]),
                       ("a category in two groups", [{"name": "A", "categories": ["X"]}, {"name": "B", "categories": ["X"]}]),
                       ("a group without a name", [{"categories": ["X"]}]),
                       ("a blank name", [{"name": "  "}]),
                       ("categories that are not a list", [{"name": "A", "categories": "X"}]),
                       ("not a list of tables", "Food"),
                       ("a list of strings", ["Food", "Other"]),
                       ("an empty list", [])):
        g, why = groups_mod.from_config(bad)
        need(g is None and why != "", "[[groups]] with %s must be refused with a reason (%r)" % (label, why))

    # the adapter: normalise() and the read-only guard
    raw_c = {"categories": [{"id": cid, "name": n, "group": {"id": "g", "name": g, "type": t}} for cid, (n, g, t) in CATS.items()]}
    raw_a = {"accounts": [{"id": "a1", "displayName": "Maple Card", "displayLastUpdatedAt": "2025-10-01T12:00:00+00:00", "hideTransactionsFromReports": False, "isManual": False, "syncDisabled": False, "deactivatedAt": None, "credential": {"updateRequired": True, "disconnectedFromDataProviderAt": None}},
                          {"id": "a2", "displayName": "Manual", "displayLastUpdatedAt": None, "hideTransactionsFromReports": True, "isManual": True, "syncDisabled": False, "deactivatedAt": None, "credential": None}]}
    raw_t = [{"id": "1", "amount": -12.34, "pending": True, "date": "2025-10-01", "hideFromReports": False, "category": {"id": "c-groc", "name": "Groceries"}, "account": {"id": "a1"}, "tags": [{"id": "x", "name": "One-off"}]},
             {"id": "1", "amount": -12.34, "pending": True, "date": "2025-10-01", "hideFromReports": False, "category": {"id": "c-groc", "name": "Groceries"}, "account": {"id": "a1"}, "tags": []},
             {"id": "2", "amount": 3100.0, "pending": False, "date": "2025-10-01", "hideFromReports": True, "category": {"id": "c-pay", "name": "Paychecks"}, "account": {"id": "a2"}, "tags": []}]
    raw_r = {"recurringTransactionItems": [{"stream": {"id": "s", "isApproximate": True, "merchant": {"id": "m", "name": "Riverbend Water"}}, "date": "2025-10-10", "isPast": False, "amount": -38.0}]}
    snap = adapter.normalise(raw_c, raw_a, raw_t, raw_r, datetime.date(2025, 1, 1), datetime.date(2025, 10, 1))
    need(len(snap["transactions"]) == 2, "normalise must count a repeated id once")
    t0 = snap["transactions"][0]
    need(t0 == {"id": "1", "date": "2025-10-01", "amount": "-12.34", "category_id": "c-groc", "category": "Groceries", "group": "Food & Dining",
                "group_type": "expense", "tags": ["One-off"], "pending": True, "hidden": False, "account_id": "a1"}, "normalise: transaction shape %r" % (t0,))
    need(snap["transactions"][-1]["group_type"] == "income" and snap["transactions"][-1]["hidden"] is True, "normalise: income row")
    need(snap["accounts"][0]["needs_reconnect"] is True and snap["accounts"][1]["hidden"] is True and snap["accounts"][1]["manual"] is True, "normalise: accounts")
    need(snap["recurring"] == [{"date": "2025-10-10", "amount": "-38.0", "name": "Riverbend Water", "is_past": False, "approximate": True}], "normalise: recurring")
    need(set(snap) == {"schema", "source", "fetched_at", "window_start", "window_end", "transactions", "accounts", "recurring"}, "normalise: keys")
    try:
        adapter.normalise({"categories": [{"name": "no id"}]}, raw_a, raw_t, raw_r, datetime.date(2025, 1, 1), datetime.date(2025, 10, 1))
        fails.append("normalise accepted a category without an id")
    except adapter.FetchError as e:
        need(e.kind == "shape", "a malformed answer must be a shape error")
    try:
        from gql import gql
        for text in (adapter.Q_CATEGORIES, adapter.Q_ACCOUNTS, adapter.Q_TRANSACTIONS, adapter.Q_RECURRING, adapter.Q_VERIFY):
            adapter.assert_query_only(gql(text))
        for bad in ('mutation M { deleteTransaction(input: {transactionId: "1"}) { deleted } }', "query A { a } mutation B { b }", "subscription S { s }"):
            try:
                adapter.assert_query_only(gql(bad))
                fails.append("the read-only guard accepted: %s" % bad[:24])
            except adapter.ReadOnlyViolation:
                pass
        try:
            adapter.assert_query_only("mutation M { x }")
            fails.append("the read-only guard accepted an unparsed string")
        except adapter.ReadOnlyViolation:
            pass
    except ImportError:
        fails.append("gql is not importable: run T9 with the engine's venv python")
    return fails


# ----------------------------------------------------------------------------------------------
# Mutants: (name, file, old text, new text). Each must make check() fail.
# ----------------------------------------------------------------------------------------------
SPEND_HEAD = '    if t.get("group_type") != "expense":\n        return False\n    if t.get("hidden") or t.get("account_id") in hidden_accounts:\n        return False\n    return not (set'
MUTANTS = [
    ("One-off purchases counted", "model.py", '        return False\n    return not (set(t.get("tags") or ()) & set(excluded_tags))', '        return False\n    return True'),
    ("transfers and card payments counted", "model.py", SPEND_HEAD, SPEND_HEAD.replace('!= "expense"', '== "income"')),
    ("income counted as negative spending", "model.py", SPEND_HEAD, SPEND_HEAD.replace('!= "expense"', '== "transfer"')),
    ("hidden transactions counted", "model.py", SPEND_HEAD, SPEND_HEAD.replace('if t.get("hidden") or t.get("account_id") in hidden_accounts:', 'if t.get("account_id") in hidden_accounts:')),
    ("a hidden account's transactions counted", "model.py", SPEND_HEAD, SPEND_HEAD.replace('if t.get("hidden") or t.get("account_id") in hidden_accounts:', 'if t.get("hidden"):')),
    ("refunds added instead of netted", "model.py", '            spend = -D(t["amount"])\n            cat =', '            spend = abs(D(t["amount"]))\n            cat ='),
    ("pending transactions dropped", "model.py", '            spend = -D(t["amount"])\n            cat =', '            if t.get("pending"):\n                continue\n            spend = -D(t["amount"])\n            cat ='),
    ("future-dated rows counted", "model.py", "            if d > today:\n                continue", "            if False:\n                continue"),
    ("last day does not take the full month", "model.py", "    return dim if d >= M else min(d, dim)", "    return min(d, dim)"),
    ("baseline ignores where the history starts", "model.py", '    start = max(parse_date(snapshot["window_start"]), first)', '    start = parse_date(snapshot["window_start"])'),
    ("baseline ignores the fetch window", "model.py", '    start = max(parse_date(snapshot["window_start"]), first)', '    start = first'),
    ("baseline takes an incomplete first month", "model.py", "        if datetime.date(y, m, 1) < start:", "        if datetime.date(y, m, 28) < start:"),
    ("baseline pinned to a hardcoded year", "model.py", "        if datetime.date(y, m, 1) < start:", "        if datetime.date(y, m, 1) < start or y < 2025:"),
    ("usual_since is ignored", "model.py", "    if since is not None:\n        start = max(start, datetime.date(since[0], since[1], 1))", "    if False:\n        start = max(start, datetime.date(since[0], since[1], 1))"),
    ("usual_since never reaches the card", "model.py", "    base = baseline_months(snapshot, today, since=settings.usual_since)\n", "    base = baseline_months(snapshot, today)\n"),
    ("usual_since drops the month it names", "model.py", "datetime.date(since[0], since[1], 1))", "datetime.date(since[0], since[1], 28))"),
    ("usual_since overrides where the history starts", "model.py", "        start = max(start, datetime.date(since[0], since[1], 1))", "        start = datetime.date(since[0], since[1], 1)"),
    ("the settings lose usual_since", "model.py", "        self.usual_since = tuple(usual_since) if usual_since else None", "        self.usual_since = None"),
    ("an excluded tag is counted anyway", "model.py", "            if not is_spending(t, self.hidden_accounts, settings.left_out_tags):", "            if not is_spending(t, self.hidden_accounts, settings.one_off_tags):"),
    ("a renamed one-off tag is ignored", "model.py", "            if is_excluded_one_off(t, self.hidden_accounts, settings.one_off_tags) and key == cur:", "            if is_excluded_one_off(t, self.hidden_accounts) and key == cur:"),
    ("the tags in force are not the configured ones", "model.py", "    months = Months(snapshot, today, settings)\n", "    months = Months(snapshot, today)\n"),
    ("no one-off tag, yet the note says one-offs are excluded", "model.py", "    elif settings.one_off_tag:\n", "    else:\n"),
    ("the configured groups are ignored", "model.py", "    grouping = settings.grouping\n", "    grouping = groups.Grouping()\n"),
    ("unlisted categories land in the first group", "groups.py", "        return (g, True) if g else (self.catch_all, False)", "        return (g, True) if g else (self.order[0], False)"),
    ("nine groups accepted", "groups.py", "    if len(tables) > MAX_GROUPS:", "    if len(tables) > MAX_GROUPS + 1:"),
    ("a category in two groups accepted", "groups.py", "            if c in mapping:\n", "            if False:\n"),
    ("a group listed twice accepted", "groups.py", "        if name in order:\n", "        if False:\n"),
    ("baseline not capped at twelve months", "model.py", "BASELINE_MAX_MONTHS = 12", "BASELINE_MAX_MONTHS = 99"),
    ("tolerance edge: at the tolerance reads over", "model.py", "        if diff > tol:\n            pace_state = \"over\"", "        if diff >= tol:\n            pace_state = \"over\""),
    ("state decided on the unrounded figure", "model.py", "        diff = fmt.whole(spent - usual_now)\n", "        diff = spent - usual_now\n"),
    ("over painted green, under painted amber", "model.py", '        hero["pace_dir"] = "over" if pace_state == "over" else "good"', '        hero["pace_dir"] = "good" if pace_state == "over" else "over"'),
    ("projection subtracts the difference", "model.py", "        projection = fmt.whole(usual_month) + diff", "        projection = fmt.whole(usual_month) - diff"),
    ("chips ranked by signed value", "model.py", "                ranked.append((-abs(diff_g), index, row))", "                ranked.append((-diff_g, index, row))"),
    ("chips without a floor", "model.py", "            if abs(diff_g) >= CHIP_MIN:", "            if abs(diff_g) >= 0:"),
    ("chips in row order, not by distance", "model.py", "             for _, _, row in sorted(ranked, key=lambda r: r[:2])[:CHIP_MAX]]", "             for _, _, row in ranked[:CHIP_MAX]]"),
    ("more than three chips", "model.py", "CHIP_MAX = 3", "CHIP_MAX = 8"),
    ("a chip shows the month's amount, not the difference", "model.py", 'chips = [{"label": row["name"], "value_text": row["vs_text"], "dir": row["dir"]}', 'chips = [{"label": row["name"], "value_text": row["value_text"], "dir": row["dir"]}'),
    ("a chip's name is not its row's", "model.py", 'chips = [{"label": row["name"], "value_text": row["vs_text"], "dir": row["dir"]}', 'chips = [{"label": row["name"].upper(), "value_text": row["vs_text"], "dir": row["dir"]}'),
    ("the over headline is worded against the month", "model.py", '            hero["pace_text"] = "%s over a usual day %d" % (arrow_text(diff), d)', '            hero["pace_text"] = "%s over a usual month" % arrow_text(diff)'),
    ("the under headline is worded against the month", "model.py", '            hero["pace_text"] = "%s under a usual day %d" % (arrow_text(diff), d)', '            hero["pace_text"] = "%s under a usual month" % arrow_text(diff)'),
    ("the headline names the month's last day, not today", "model.py", '            hero["pace_text"] = "%s over a usual day %d" % (arrow_text(diff), d)', '            hero["pace_text"] = "%s over a usual day %d" % (arrow_text(diff), M)'),
    ("the under headline names tomorrow", "model.py", '            hero["pace_text"] = "%s under a usual day %d" % (arrow_text(diff), d)', '            hero["pace_text"] = "%s under a usual day %d" % (arrow_text(diff), d + 1)'),
    ("British spelling back on the card", "model.py", '            parts.append("bar color from day %d" % (grace_days + 1))', '            parts.append("bar colour from day %d" % (grace_days + 1))'),
    ("group row tolerance off by one", "model.py", "            if abs(diff_g) < ROW_TOL:", "            if abs(diff_g) <= ROW_TOL:"),
    ("group weights on the net total", "model.py", "    positive = sum((max(v, ZERO) for v in g_now.values()), ZERO)", "    positive = sum((v for v in g_now.values()), ZERO)"),
    ("band from the wrong quantile", "model.py", 'band.append((quantile(vals, Decimal("0.25")), quantile(vals, Decimal("0.75"))))', 'band.append((quantile(vals, Decimal("0.10")), quantile(vals, Decimal("0.75"))))'),
    ("chart range ignores the band", "model.py", "    everything = cur_series + usual_series + [v for pair in band for v in pair]", "    everything = cur_series + usual_series"),
    ("next bill takes income and past items", "model.py", '        if rd < today or r.get("is_past") or amt >= 0:', "        if rd < today:"),
    ("'then N more' runs past the month's end", "model.py", "        rest = [u for u in upcoming[1:] if u[0] <= month_end]", "        rest = [u for u in upcoming[1:]]"),
    ("bank data age from the newest account", "model.py", "            if oldest is None or stamp < oldest[0]:", "            if oldest is None or stamp > oldest[0]:"),
    ("a dormant account speaks for the banks", "model.py", '        if a["id"] not in active_ids or a.get("manual") or a.get("hidden"):', '        if a.get("manual") or a.get("hidden"):'),
    ("grace period ignored: the face is tinted on day 1", "model.py", "    if day is not None and day <= grace_days:\n        return \"plain\"", "    if False:\n        return \"plain\""),
    ("grace period off by one: day N is already tinted", "model.py", "    if day is not None and day <= grace_days:\n        return \"plain\"", "    if day is not None and day < grace_days:\n        return \"plain\""),
    ("grace period one day too long", "model.py", "    if day is not None and day <= grace_days:\n        return \"plain\"", "    if day is not None and day <= grace_days + 1:\n        return \"plain\""),
    ("grace period hides stale data", "model.py", "    if age > STALE_AFTER:\n        return \"stale\"\n    if day is not None and day <= grace_days:\n        return \"plain\"", "    if day is not None and day <= grace_days:\n        return \"plain\"\n    if age > STALE_AFTER:\n        return \"stale\""),
    ("grace period also takes the card's tint", "model.py", '        hero["pace_dir"] = "over" if pace_state == "over" else "good"', '        hero["pace_dir"] = "none" if d <= grace_days else ("over" if pace_state == "over" else "good")'),
    ("the note does not say the color is waiting", "model.py", "        if d <= grace_days:\n            parts.append(\"bar color from day %d\" % (grace_days + 1))", "        if False:\n            parts.append(\"bar color from day %d\" % (grace_days + 1))"),
    ("the grace setting does not reach the face", "model.py", "    state = face_for(pace_state, age, now.day, grace_days)", "    state = face_for(pace_state, age, now.day)"),
    ("default grace is zero", "model.py", "PACE_GRACE_DAYS = 3 ", "PACE_GRACE_DAYS = 0 "),
    ("stale threshold moved to 30 h", "model.py", "STALE_AFTER = 3 * 3600", "STALE_AFTER = 30 * 3600"),
    ("data older than 36 h still painted", "model.py", "    if age > BROKEN_AFTER:\n        return broken_doc(", "    if False:\n        return broken_doc("),
    ("the broken document carries the butterfly", "model.py", '    return {"v": 1, "text": BROKEN_GLYPH, "face": {"state": "broken", "reason": reason},', '    return {"v": 1, "text": GLYPH, "face": {"state": "broken", "reason": reason},'),
    ("whole dollars rounded half-even", "fmt.py", 'q = Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)\n    return q + 0', 'q = Decimal(value).quantize(Decimal("1"))\n    return q + 0'),
    ("negative amounts lose their sign", "fmt.py", '    return "%s$%s" % (MINUS if q < 0 else "", "{:,}".format(abs(int(q))))', '    return "%s$%s" % ("", "{:,}".format(abs(int(q))))'),
    ("a category moved to another group", "groups.py", '    "Gas": "Transport",', '    "Gas": "Home & utilities",'),
    ("swatches follow rank, not group", "groups.py", "        self.swatch = dict(zip(self.order, PALETTE))", "        self.swatch = dict(zip(self.order, reversed(PALETTE)))"),
    ("normalise keeps a repeated row", "adapter.py", "            if t[\"id\"] in seen:\n                continue", "            if False:\n                continue"),
    ("the guard lets a mutation through", "adapter.py", "        if op.operation != OperationType.QUERY:", "        if op.operation == OperationType.SUBSCRIPTION:"),
    ("hidden flag dropped in normalise", "adapter.py", '                "hidden": bool(t.get("hideFromReports")),', '                "hidden": False,'),
]


def main():
    if len(sys.argv) < 2:
        print("usage: t9_monarch_model.py <engine dir>")
        return 2
    if sys.argv[1] == "--check":
        fails = check(os.path.abspath(sys.argv[2]))
        print(json.dumps(fails))
        return 0
    engine = os.path.abspath(sys.argv[1])

    def run(dirpath):
        p = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--check", dirpath], capture_output=True, text=True, timeout=900)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return ["the check crashed: " + (p.stderr.strip().splitlines() or ["no output"])[-1]]

    fails = run(engine)
    n_scen, n_pin = len(scenarios()), len(pinned_cases())
    print("T9 candidate: %s" % engine)
    print("T9 scenarios against the oracle: %d, hand-pinned cases: %d" % (n_scen, n_pin))
    for f in fails:
        print("  FAIL " + f)
    print("T9 candidate: %s" % ("PASS" if not fails else "%d FAILED" % len(fails)))
    rc = 1 if fails else 0
    if "--no-mutants" in sys.argv:
        return rc

    killed = 0
    for name, fname, old, new in MUTANTS:
        tmp = tempfile.mkdtemp(prefix="t9-mutant-")
        try:
            shutil.copytree(os.path.join(engine, "monarchnow"), os.path.join(tmp, "monarchnow"), ignore=shutil.ignore_patterns("__pycache__"))
            path = os.path.join(tmp, "monarchnow", fname)
            src = open(path, encoding="utf-8").read()
            if src.count(old) != 1 or old == new:
                print("  ERROR mutant %r did not apply (%d matches)" % (name, src.count(old)))
                rc = 1
                continue
            open(path, "w", encoding="utf-8").write(src.replace(old, new))
            mf = run(tmp)
            if mf and any(f.startswith("the check crashed") for f in mf) and not any("ReadOnly" in f or "Error" in f for f in mf):
                print("  mutant killed (crash)    : %s" % name)
                killed += 1
            elif mf:
                killed += 1
                print("  mutant killed (%2d checks): %s" % (len(mf), name))
            else:
                print("  MUTANT SURVIVED: %s" % name)
                rc = 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("T9 mutants killed: %d/%d" % (killed, len(MUTANTS)))
    print("T9: %s" % ("ALL PASS" if rc == 0 else "FAILED"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
