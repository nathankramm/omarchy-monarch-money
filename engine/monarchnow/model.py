"""The model: a snapshot of Monarch data + a clock -> every string the bar face and the hover
card paint. Pure: no I/O, no network, no clock of its own. Decimal throughout.

WHAT COUNTS AS SPENDING (one rule, used for this month and for the usual month alike)
    a transaction whose category sits in an EXPENSE-type category group
    and is not hidden from reports (the transaction's own flag, or its account's)
    and carries neither the one-off tag nor any other excluded tag (config: one_off_tag,
        excluded_tags; the default is one tag, "One-off").
  Transfers and credit card payments are TRANSFER-type groups and income is INCOME-type, so they
  fall out by type, the way Monarch's own Spending report drops them. A refund is a positive
  amount in an expense category and nets against spending. Pending transactions count.

THE USUAL MONTH
  The average cumulative-by-day spending curve over the recent complete months (baseline):
      usual(d) = mean over baseline months m of  cum_m(min(d, days_in(m)))
  The baseline is the user's OWN history, never a hardcoded period: the complete months before
  this one that the data covers, the newest twelve at most. The month in which the history
  starts part-way (its first transaction is after the 1st) is not complete and is left out.
  The one thing the user may set is a floor (config: usual_since = "YYYY-MM"): no month before
  it is used, for a household whose older months are not worth comparing with.
  and on the last day of the current month every baseline month gives its full total, so the
  curve ends on the average month's total whatever the month lengths.

PACE (the face's tint, the hero line)
      diff  = whole(spent so far - usual(today's day))            # whole dollars, as shown;
                                                                  # worded "over/under a usual day N"
      over  when diff >  tol,   under when diff < -tol,   else at
      tol   = whole(max(25, 0.5% of the usual month's total))
  The state is decided on the SHOWN whole-dollar figure, so the text and the tint cannot disagree.
  Green = under or at; amber = over. Nothing on this card is red.

THE GRACE PERIOD (in the first days of a month a single charge decides the color)
  On days 1..pace_grace_days of a month (default 3; 0 switches it off) the FACE is "plain": a
  butterfly in the bar's own color, neither green nor amber. Only the face waits. The card is
  computed and tinted exactly as on any other day, and its note says from which day the bar
  takes a color, so the neutral butterfly cannot be read as "on pace".
      projection = whole(usual month's total) + diff              # sums of the shown figures
"""
import datetime
from decimal import Decimal

from . import fmt, groups

GLYPH = "\U000f1589"          # nf-md-butterfly: a generic butterfly from Nerd Fonts' Material Design
                              # set, not Monarch Money's logo. 8-digit escape on purpose.
BROKEN_GLYPH = "\U000f0338"   # nf-md-link_off: the feed is broken. Never a butterfly.
assert (len(GLYPH), ord(GLYPH)) == (1, 0xF1589), "GLYPH is not U+F1589"
assert (len(BROKEN_GLYPH), ord(BROKEN_GLYPH)) == (1, 0xF0338), "BROKEN_GLYPH is not U+F0338"

STALE_AFTER = 3 * 3600        # data older than this: the butterfly goes grey
BROKEN_AFTER = 36 * 3600      # older than this it is not "this month's spending" any more

ONE_OFF_TAG = "One-off"       # config: one_off_tag. Left out of spending and counted in the note
EXCLUDED_TAGS = ()            # config: excluded_tags. Left out of spending, without a word
BASELINE_MAX_MONTHS = 12
PACE_TOL_FLOOR = Decimal("25")
PACE_TOL_SHARE = Decimal("0.005")
ROW_TOL = Decimal("10")       # a group within $10 of usual reads "≈", in the plain color
CHIP_MIN = Decimal("20")      # a group nearer to usual than this is not worth a chip
assert CHIP_MIN >= ROW_TOL    # else a chip could show a figure where its row shows "≈"
CHIP_MAX = 3
BAND_MIN_MONTHS = 4           # the middle-half band needs a few months to mean anything
BILL_WINDOW_DAYS = 45
ACTIVE_ACCOUNT_DAYS = 60
CHART_PAD = Decimal("0.08")
PACE_GRACE_DAYS = 3           # days 1..N of a month: the butterfly stays neutral (config: pace_grace_days)

OPEN_TEXT = "Open Monarch"    # the card's one control; the widget runs bin/monarch-open

ZERO = Decimal("0")


def D(x):
    return x if isinstance(x, Decimal) else Decimal(str(x))


def parse_date(s):
    return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


class Settings:
    """What the config file can change besides the grace period, with the defaults. Pure data."""

    def __init__(self, one_off_tag=ONE_OFF_TAG, excluded_tags=EXCLUDED_TAGS, grouping=None, usual_since=None):
        self.one_off_tag = one_off_tag or ""
        self.excluded_tags = tuple(excluded_tags)
        self.grouping = grouping or groups.Grouping()
        self.usual_since = tuple(usual_since) if usual_since else None    # (year, month) or None

    @property
    def one_off_tags(self):
        return (self.one_off_tag,) if self.one_off_tag else ()

    @property
    def left_out_tags(self):
        """Every tag that takes a transaction out of spending."""
        return self.one_off_tags + self.excluded_tags


def is_spending(t, hidden_accounts, excluded_tags=(ONE_OFF_TAG,)):
    if t.get("group_type") != "expense":
        return False
    if t.get("hidden") or t.get("account_id") in hidden_accounts:
        return False
    return not (set(t.get("tags") or ()) & set(excluded_tags))


def is_excluded_one_off(t, hidden_accounts, excluded_tags=(ONE_OFF_TAG,)):
    """Would be spending but for the one-off tag: what the note counts."""
    if t.get("group_type") != "expense":
        return False
    if t.get("hidden") or t.get("account_id") in hidden_accounts:
        return False
    return bool(set(t.get("tags") or ()) & set(excluded_tags))


class Months:
    """Daily spending per month, in total and per category, from the spending transactions."""

    def __init__(self, snapshot, today, settings=None):
        settings = settings or Settings()
        self.hidden_accounts = {a["id"] for a in snapshot.get("accounts", []) if a.get("hidden")}
        self.total = {}        # (y, m) -> {day: Decimal}
        self.by_cat = {}       # (y, m) -> {category: {day: Decimal}}
        self.pending_now = 0
        self.one_off_count = 0
        self.one_off_sum = ZERO
        cur = (today.year, today.month)
        for t in snapshot.get("transactions", []):
            d = parse_date(t["date"])
            if d > today:
                continue           # nothing dated after "today" is known yet (and --as-of relies on it)
            key = (d.year, d.month)
            if is_excluded_one_off(t, self.hidden_accounts, settings.one_off_tags) and key == cur:
                self.one_off_count += 1
                self.one_off_sum += -D(t["amount"])
            if not is_spending(t, self.hidden_accounts, settings.left_out_tags):
                continue
            spend = -D(t["amount"])
            cat = t.get("category") or "Uncategorized"
            self.total.setdefault(key, {})
            self.total[key][d.day] = self.total[key].get(d.day, ZERO) + spend
            self.by_cat.setdefault(key, {}).setdefault(cat, {})
            self.by_cat[key][cat][d.day] = self.by_cat[key][cat].get(d.day, ZERO) + spend
            if key == cur and t.get("pending"):
                self.pending_now += 1

    @staticmethod
    def cum(days, upto):
        return sum((v for day, v in days.items() if day <= upto), ZERO)

    def month_cum(self, key, upto):
        return self.cum(self.total.get(key, {}), upto)

    def cat_cum(self, key, cat, upto):
        return self.cum(self.by_cat.get(key, {}).get(cat, {}), upto)


def history_start(snapshot):
    """The date of the earliest transaction of any kind in the snapshot, or None when there is none."""
    dates = [t["date"] for t in snapshot.get("transactions", [])]
    return parse_date(min(dates)) if dates else None


def baseline_months(snapshot, today, max_months=BASELINE_MAX_MONTHS, since=None):
    """The recent complete months of the user's own history: newest first, at most max_months,
    returned oldest first. A month counts only if the fetch window AND the history both start on
    or before its first day, so the month in which the history begins part-way is left out.
    `since` (year, month) is the user's own floor: no month before it counts."""
    first = history_start(snapshot)
    if first is None:
        return []
    start = max(parse_date(snapshot["window_start"]), first)
    if since is not None:
        start = max(start, datetime.date(since[0], since[1], 1))
    out = []
    y, m = today.year, today.month
    while len(out) < max_months:
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        if datetime.date(y, m, 1) < start:
            break
        out.append((y, m))
    return list(reversed(out))


def aligned_day(d, M, month_key):
    """The day of a baseline month that stands for day d of an M-day current month."""
    dim = fmt.days_in_month(*month_key)
    return dim if d >= M else min(d, dim)


def mean(values):
    return sum(values, ZERO) / Decimal(len(values))


def quantile(sorted_values, q):
    """Linear-interpolated quantile of an ascending list (the 'linear' / type-7 rule)."""
    n = len(sorted_values)
    pos = Decimal(n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def arrow_text(diff_whole):
    """'▲ $84' / '▼ $120' for a whole-dollar difference that is not zero."""
    return "%s %s" % (fmt.UP if diff_whole > 0 else fmt.DOWN, fmt.dollars_abs(diff_whole))


def build_card(snapshot, now, grace_days=PACE_GRACE_DAYS, settings=None):
    """The card for a snapshot at `now` (tz-aware). Returns (card, pace_state)."""
    settings = settings or Settings()
    grouping = settings.grouping
    today = now.date()
    cur = (today.year, today.month)
    M = fmt.days_in_month(*cur)
    d = today.day
    months = Months(snapshot, today, settings)
    base = baseline_months(snapshot, today, since=settings.usual_since)
    has_usual = len(base) > 0
    mon = fmt.MONTHS[today.month - 1]

    spent = months.month_cum(cur, d)

    def usual_total_at(k):
        return mean([months.month_cum(b, aligned_day(k, M, b)) for b in base])

    def usual_cat_at(cat, k):
        return mean([months.cat_cum(b, cat, aligned_day(k, M, b)) for b in base])

    # ---- hero
    hero = {"total_text": fmt.dollars(spent), "total_label": "spent this month", "pill_text": "%s %s day %d of %d" % (mon, fmt.DOT, d, M),
            "pace_text": "", "pace_dir": "none", "detail_text": ""}
    pace_state = "none"
    if has_usual:
        usual_now = usual_total_at(d)
        usual_month = usual_total_at(M)
        diff = fmt.whole(spent - usual_now)
        tol = fmt.whole(max(PACE_TOL_FLOOR, PACE_TOL_SHARE * usual_month))
        if diff > tol:
            pace_state = "over"
            hero["pace_text"] = "%s over a usual day %d" % (arrow_text(diff), d)
        elif diff < -tol:
            pace_state = "under"
            hero["pace_text"] = "%s under a usual day %d" % (arrow_text(diff), d)
        else:
            pace_state = "at"
            hero["pace_text"] = "%s on a usual month's pace" % fmt.APPROX
        hero["pace_dir"] = "over" if pace_state == "over" else "good"
        projection = fmt.whole(usual_month) + diff
        hero["detail_text"] = "on pace for %s %s usual %s" % (fmt.dollars(projection), fmt.DOT, fmt.dollars(usual_month))
    else:
        hero["pace_text"] = "no usual month yet (it needs one complete month)"

    # ---- chart: normalised points; the painter multiplies by pixels and nothing else
    chart = None
    cur_series = [ZERO] + [months.month_cum(cur, k) for k in range(1, d + 1)]
    usual_series = ([ZERO] + [usual_total_at(k) for k in range(1, M + 1)]) if has_usual else []
    band = []
    if len(base) >= BAND_MIN_MONTHS:
        for k in range(1, M + 1):
            vals = sorted(months.month_cum(b, aligned_day(k, M, b)) for b in base)
            band.append((quantile(vals, Decimal("0.25")), quantile(vals, Decimal("0.75"))))
    everything = cur_series + usual_series + [v for pair in band for v in pair]
    lo, hi = min(everything), max(everything)
    if hi > lo:
        span = hi - lo
        top = hi + span * CHART_PAD
        bottom = lo - span * CHART_PAD if lo < 0 else lo
        full = top - bottom

        def ny(v):
            return round(float((v - bottom) / full), 4)

        def nx(k):
            return round(k / M, 4)

        legend = [{"kind": "line", "text": "%s so far" % mon}]
        if has_usual:
            legend.append({"kind": "dash", "text": "usual month %s" % fmt.dollars(usual_series[M])})
        if band:
            legend.append({"kind": "band", "text": "middle half of months"})
        chart = {
            "current": [{"x": nx(k), "y": ny(v)} for k, v in enumerate(cur_series)],
            "usual": [{"x": nx(k), "y": ny(v)} for k, v in enumerate(usual_series)],
            "band": [{"x": nx(k + 1), "lo": ny(p[0]), "hi": ny(p[1])} for k, p in enumerate(band)],
            "today_x": nx(d),
            "today_y": ny(cur_series[d]),
            "zero_y": ny(ZERO),
            "dir": "over" if pace_state == "over" else ("good" if has_usual else "none"),
            # inside the plot: the top of the scale, the bottom only when it is not zero, the span
            "max_text": fmt.dollars(hi),
            "min_text": fmt.dollars(lo) if lo != 0 else "",
            "span_text": "%s 1%s%d" % (mon, fmt.NDASH, M),
            # under the plot: one entry per mark, so no series is told apart by color alone
            "legend": legend,
        }

    # ---- the note
    parts = []
    if months.one_off_count:
        parts.append("%d one-off%s excluded (%s)" % (months.one_off_count, "" if months.one_off_count == 1 else "s",
                                                     fmt.dollars(months.one_off_sum)))
    elif settings.one_off_tag:
        parts.append("one-offs excluded")
    if months.pending_now:
        parts.append("%d pending included" % months.pending_now)
    if has_usual:
        parts.append("usual = %s average" % fmt.month_span(base[0], base[-1]))
        if d <= grace_days:
            parts.append("bar color from day %d" % (grace_days + 1))
    note_text = (" %s " % fmt.DOT).join(parts)

    # ---- categories: this month so far, usual by now, the difference
    cats = set(months.by_cat.get(cur, {}))
    for b in base:
        cats |= set(months.by_cat.get(b, {}))
    cat_rows = []
    unmapped = set()
    for cat in sorted(cats):
        now_c = months.cat_cum(cur, cat, d)
        usual_c = usual_cat_at(cat, d) if has_usual else ZERO
        group, mapped = grouping.group_of(cat)
        if not mapped:
            unmapped.add(cat)
        cat_rows.append({"cat": cat, "group": group, "now": now_c, "usual": usual_c})

    # ---- groups: fixed order, color by group
    g_now = {g: ZERO for g in grouping.order}
    g_usual = {g: ZERO for g in grouping.order}
    for r in cat_rows:
        g_now[r["group"]] += r["now"]
        g_usual[r["group"]] += r["usual"]
    positive = sum((max(v, ZERO) for v in g_now.values()), ZERO)
    group_rows = []
    ranked = []
    for index, g in enumerate(grouping.order):
        row = {"name": g, "swatch": grouping.swatch[g], "value_text": fmt.dollars(g_now[g]),
               "vs_text": "", "dir": "none",
               "weight_ratio": round(float(max(g_now[g], ZERO) / positive), 4) if positive > 0 else 0.0}
        if has_usual:
            diff_g = fmt.whole(g_now[g] - g_usual[g])
            if abs(diff_g) < ROW_TOL:
                row["vs_text"], row["dir"] = fmt.APPROX, "flat"
            else:
                row["vs_text"], row["dir"] = arrow_text(diff_g), ("over" if diff_g > 0 else "good")
            if abs(diff_g) >= CHIP_MIN:
                ranked.append((-abs(diff_g), index, row))
        group_rows.append(row)

    # ---- chips: the GROUPS furthest from usual, named and figured exactly as their rows are
    # (one vocabulary on the card: a chip never names a Monarch category that no row shows).
    # CHIP_MIN is above ROW_TOL, so a chip's row always shows the same figure.
    chips = [{"label": row["name"], "value_text": row["vs_text"], "dir": row["dir"]}
             for _, _, row in sorted(ranked, key=lambda r: r[:2])[:CHIP_MAX]]

    # ---- the next recurring bill
    month_end = datetime.date(today.year, today.month, M)
    upcoming = []
    for r in snapshot.get("recurring", []):
        rd = parse_date(r["date"])
        amt = D(r["amount"])
        if rd < today or r.get("is_past") or amt >= 0:
            continue
        upcoming.append((rd, amt, r.get("name") or "", bool(r.get("approximate"))))
    upcoming.sort()
    footer = {"left_text": "", "left_sub_text": "", "right_text": "", "attention_text": ""}
    if upcoming:
        rd, amt, name, approx = upcoming[0]
        footer["left_text"] = "Next bill: %s %s %s %s %s%s" % (name, fmt.DOT, fmt.month_day(rd), fmt.DOT,
                                                             "~" if approx else "", fmt.cents(-amt))
        rest = [u for u in upcoming[1:] if u[0] <= month_end]
        if rest:
            footer["left_sub_text"] = "then %d more by %s %s %s" % (
                len(rest), fmt.month_day(month_end), fmt.DOT, fmt.dollars(sum((-u[1] for u in rest), ZERO)))
    else:
        footer["left_text"] = "No recurring bills in the next %d days" % BILL_WINDOW_DAYS

    # ---- the two clocks: how old the banks' data is, and when Monarch was last checked
    fetched = datetime.datetime.fromtimestamp(snapshot["fetched_at"], now.tzinfo)
    cutoff = today - datetime.timedelta(days=ACTIVE_ACCOUNT_DAYS)
    active_ids = {t.get("account_id") for t in snapshot.get("transactions", [])
                  if t.get("group_type") == "expense" and cutoff <= parse_date(t["date"]) <= today}
    oldest = None
    attention = []
    for a in snapshot.get("accounts", []):
        if a["id"] not in active_ids or a.get("manual") or a.get("hidden"):
            continue
        if a.get("needs_reconnect") or a.get("sync_disabled"):
            attention.append(a.get("name") or "an account")
        if a.get("updated_at"):
            stamp = datetime.datetime.fromisoformat(a["updated_at"])
            if oldest is None or stamp < oldest[0]:
                oldest = (stamp, a.get("name") or "")
    right = []
    if oldest is not None:
        age = (fetched - oldest[0]).total_seconds()
        word = fmt.age_text(age)
        if word == "just now":
            right.append("bank data just synced")
        elif age > 24 * 3600:
            right.append("bank data %s old (%s)" % (word, oldest[1]))
        else:
            right.append("bank data %s old" % word)
    right.append("checked %s" % (fmt.clock(fetched) if fetched.date() == today else fetched.strftime("%a %H:%M")))
    footer["right_text"] = (" %s " % fmt.DOT).join(right)
    if attention:
        footer["attention_text"] = "Reconnect in Monarch: %s" % ", ".join(sorted(attention))

    card = {"hero": hero, "banner_text": "", "hint_text": "", "chart": chart, "note_text": note_text,
            "chips_label": "Furthest", "chips": chips, "groups": group_rows, "footer": footer,
            "open_text": OPEN_TEXT, "unmapped": sorted(unmapped)}
    return card, pace_state


def face_for(pace_state, age, day=None, grace_days=PACE_GRACE_DAYS):
    """The face state from the pace, the data's age (seconds since the last good fetch) and the
    day of the month. Age comes first: stale and broken are never hidden by the grace period."""
    if age > BROKEN_AFTER:
        return "broken"
    if age > STALE_AFTER:
        return "stale"
    if day is not None and day <= grace_days:
        return "plain"
    return {"over": "over", "under": "good", "at": "good"}.get(pace_state, "plain")


def tooltip_text(card, face_state):
    """The plain-text fallback (faceHover: "text"): the same strings, aligned for a monospace font."""
    lines = []
    if card.get("banner_text"):
        lines += [card["banner_text"], ""]
    hero = card.get("hero")
    if hero:
        lines.append("Monarch %s %s" % (fmt.DOT, hero["pill_text"]))
        lines.append("%s %s" % (hero["total_text"], hero["total_label"]))
        if hero["pace_text"]:
            lines.append(hero["pace_text"])
        if hero["detail_text"]:
            lines.append(hero["detail_text"])
    rows = card.get("groups") or []
    if rows:
        lines.append("")
        # Every row padded to one width: the shell's tooltip centers each line, and lines of
        # equal length keep their columns.
        w_name = max(len(r["name"]) for r in rows)
        w_val = max(len(r["value_text"]) for r in rows)
        w_vs = max(len(r["vs_text"]) for r in rows)
        for r in rows:
            lines.append("%s  %s  %s" % (r["name"].ljust(w_name), r["value_text"].rjust(w_val), r["vs_text"].ljust(w_vs)))
    chips = card.get("chips") or []
    if chips:
        lines += ["", "%s: %s" % (card["chips_label"], ("  %s  " % fmt.DOT).join("%s %s" % (c["label"], c["value_text"]) for c in chips))]
    if card.get("note_text"):
        lines += ["", card["note_text"]]
    foot = card.get("footer") or {}
    tail = [foot.get(k) for k in ("left_text", "left_sub_text", "attention_text", "right_text") if foot.get(k)]
    if tail:
        lines += [""] + tail
    if card.get("hint_text"):
        lines += ["", card["hint_text"]]
    return "\n".join(lines)


def broken_doc(reason, hint=""):
    """The whole output when there is nothing trustworthy to paint."""
    card = {"hero": None, "banner_text": reason, "hint_text": hint, "chart": None, "note_text": "",
            "chips_label": "", "chips": [], "groups": [], "footer": None, "open_text": OPEN_TEXT, "unmapped": []}
    return {"v": 1, "text": BROKEN_GLYPH, "face": {"state": "broken", "reason": reason},
            "tooltip": tooltip_text(card, "broken"), "card": card}


def build_doc(snapshot, now, problem="", grace_days=PACE_GRACE_DAYS, settings=None):
    """The stdout document for a snapshot at `now`. `problem` is the last fetch failure in words,
    shown on the card whenever the data is being served from cache because of it."""
    age = now.timestamp() - snapshot["fetched_at"]
    if age > BROKEN_AFTER:
        return broken_doc("Monarch data is %s old%s" % (fmt.age_text(age), (": " + problem) if problem else ""),
                          "It refreshes by itself once Monarch answers; `monarch-now status` says why it has not.")
    card, pace_state = build_card(snapshot, now, grace_days, settings)
    state = face_for(pace_state, age, now.day, grace_days)
    if state == "stale":
        card["banner_text"] = "Data is %s old%s" % (fmt.age_text(age), (": " + problem) if problem else "")
    elif problem:
        card["banner_text"] = "Last check failed (%s); showing data from %s ago" % (problem, fmt.age_text(age))
    return {"v": 1, "text": GLYPH, "face": {"state": state, "reason": card["banner_text"]},
            "tooltip": tooltip_text(card, state), "card": card}
