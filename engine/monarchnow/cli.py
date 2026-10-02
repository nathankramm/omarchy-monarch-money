"""monarch-now: the command the bar runs, and a few verbs for a terminal.

    monarch-now                 the bar tick: ONE JSON line on stdout (text, tooltip, face, card).
                                Fetches from Monarch at most hourly, behind a non-blocking lock;
                                otherwise it reads the cache and recomputes for the current clock.
    monarch-now --refresh       the same, but fetch now (the IPC refresh / middle click). Floor: 60 s.
    monarch-now login [--paste] sign in (see login.py)
    monarch-now logout          forget the session
    monarch-now status          what the engine knows, WITHOUT amounts
    monarch-now check [FROM TO] spending over a range by this engine's rule, with what was left
                                out, to set beside a Monarch report that uses the same filters
    monarch-now --as-of ISO     compute for a pinned clock from the cache (never fetches)
    monarch-now --snapshot FILE compute from a snapshot file instead of the cache (never fetches)

FACE STATES (the bar shows the glyph only, never a number)
    good / over / plain   a butterfly: data no older than 3 h (plain = no usual month yet)
    stale                 a butterfly, greyed: the data is 3 h to 36 h old
    broken                a broken-link glyph: no session, no data, or data older than 36 h
  The tick ALWAYS prints a document when it can say what is wrong; the widget treats no output,
  unparsable output or an unknown state as broken too. A butterfly is never painted over bad data.
"""
import datetime
import json
import re
import sys
import time
import tomllib

from . import adapter, fmt, groups, model, secret, store

FETCH_INTERVAL = 3600          # at most hourly
RETRY_AFTER_FAILURE = 600      # a failed check is retried after ten minutes, not every tick
RETRY_AFTER_AUTH = 6 * 3600    # a refused session is not retried all day; `login` clears it
REFRESH_FLOOR = 60             # --refresh cannot be turned into a hammer

PROBLEM = {
    "auth": "Monarch signed this session out",
    "network": "Monarch could not be reached",
    "shape": "Monarch's answer changed shape",
    "keyring": "the keyring could not be read",
    "no-session": "not signed in",
}
SIGN_IN_HINT = "Sign in: run `monarch-now login` in a terminal."


CONFIG_KEYS = ("pace_grace_days", "one_off_tag", "excluded_tags", "usual_since", "groups")
USUAL_SINCE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def load_config():
    """The optional config file, ~/.config/monarch-now/config.toml (config.example.toml has every key):
        pace_grace_days   whole number 0..31, default 3: on days 1..N of a month the butterfly
                          stays neutral (0 = color from day 1).
        one_off_tag       the Monarch tag that marks a one-off purchase (default "One-off"): left
                          out of spending and counted in the card's note. "" = no such tag.
        excluded_tags     more tags whose transactions are left out of spending (default none).
        usual_since       "YYYY-MM": the earliest month the usual month may use (default unset:
                          the newest twelve complete months of the history).
        [[groups]]        the card's rows: name + categories, in order (default: groups.py).
    A missing file is the defaults. A file that cannot be read, or a value that is not what its key
    takes, is NOT guessed at: that key's default is used and `problem` says so (status prints it)."""
    cfg = {"pace_grace_days": model.PACE_GRACE_DAYS, "settings": model.Settings(), "problem": ""}
    path = store.config_path()
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError):
        cfg["problem"] = "config.toml could not be read; defaults in use"
        return cfg
    more = []
    if "pace_grace_days" in doc:
        v = doc["pace_grace_days"]
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 31:
            cfg["pace_grace_days"] = v
        else:
            cfg["problem"] = "pace_grace_days must be a whole number from 0 to 31; the default (%d) is in use" % model.PACE_GRACE_DAYS
    one_off, excluded, grouping, usual_since = model.ONE_OFF_TAG, model.EXCLUDED_TAGS, None, None
    if "one_off_tag" in doc:
        v = doc["one_off_tag"]
        if isinstance(v, str):
            one_off = v.strip()
        else:
            more.append('one_off_tag must be a tag name in quotes; the default ("%s") is in use' % model.ONE_OFF_TAG)
    if "excluded_tags" in doc:
        v = doc["excluded_tags"]
        if isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v):
            excluded = tuple(x.strip() for x in v)
        else:
            more.append("excluded_tags must be a list of tag names; none is in use")
    if "usual_since" in doc:
        v = doc["usual_since"]
        m = USUAL_SINCE.match(v) if isinstance(v, str) else None
        if m and int(m.group(1)) >= 1:
            usual_since = (int(m.group(1)), int(m.group(2)))
        else:
            more.append('usual_since must be a month written "YYYY-MM", in quotes; it is not in use')
    if "groups" in doc:
        grouping, why = groups.from_config(doc["groups"])
        if grouping is None:
            more.append("groups: %s; the default groups are in use" % why)
    unknown = sorted(k for k in doc if k not in CONFIG_KEYS)
    if unknown:
        more.append("unknown key%s ignored: %s" % ("" if len(unknown) == 1 else "s", ", ".join(unknown)))
    cfg["settings"] = model.Settings(one_off, excluded, grouping, usual_since)
    cfg["problem"] = "; ".join(p for p in [cfg["problem"]] + more if p)
    return cfg


HISTORY_LEAD_MONTHS = 1        # fetched ahead of the twelve, never part of the usual month


def fetch_window(today):
    """The months the usual month can use (the twelve complete ones before this one) plus one
    lead month, through today. The lead month is how the model tells a long history from one that
    starts inside the window: a transaction older than the first usable month proves that month
    is complete (model.baseline_months)."""
    y, m = today.year, today.month
    for _ in range(model.BASELINE_MAX_MONTHS + HISTORY_LEAD_MONTHS):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return datetime.date(y, m, 1), today


def want_fetch(snapshot, state, now_ts, force):
    last_attempt = state.get("last_attempt", 0)
    since = now_ts - last_attempt
    if force:
        return since >= REFRESH_FLOOR
    if state.get("last_error") == "auth" or state.get("last_error") == "no-session":
        return since >= RETRY_AFTER_AUTH
    if state.get("last_error"):
        return since >= RETRY_AFTER_FAILURE
    if snapshot is None:
        return since >= RETRY_AFTER_FAILURE or last_attempt == 0
    return now_ts - snapshot["fetched_at"] >= FETCH_INTERVAL


def try_fetch(state, now):
    """One attempt. Returns (snapshot or None). Updates and saves `state`; logs counts only."""
    started = time.time()
    state["last_attempt"] = now.timestamp()
    try:
        cookies = secret.load()
    except secret.KeyringError:
        state["last_error"] = "keyring"
        store.write_state(state)
        store.log("fetch", result="keyring")
        return None
    if not cookies:
        state["last_error"] = "no-session"
        store.write_state(state)
        store.log("fetch", result="no-session")
        return None
    start, end = fetch_window(now.date())
    try:
        snap = adapter.fetch_snapshot(cookies, start, end, now.date())
    except adapter.FetchError as e:
        state["last_error"] = e.kind
        state["last_error_detail"] = e.detail
        store.write_state(state)
        store.log("fetch", result=e.kind, detail=e.detail.replace(" ", "_"), seconds="%.1f" % (time.time() - started))
        return None
    store.write_json_private(store.snapshot_path(), snap)
    state.pop("last_error", None)
    state.pop("last_error_detail", None)
    state["last_ok"] = snap["fetched_at"]
    store.write_state(state)
    store.log("fetch", result="ok", transactions=len(snap["transactions"]), accounts=len(snap["accounts"]),
              recurring=len(snap["recurring"]), seconds="%.1f" % (time.time() - started))
    return snap


def tick(now, force=False):
    """The document for the bar, fetching first when it is time to."""
    snapshot = store.read_snapshot()
    state = store.read_state()
    if want_fetch(snapshot, state, now.timestamp(), force):
        with store.Lock() as held:
            if held:
                fresh = try_fetch(state, now)
                if fresh is not None:
                    snapshot = fresh
    problem = PROBLEM.get(state.get("last_error") or "", "")
    if snapshot is None:
        why = problem or "no Monarch data yet"
        hint = SIGN_IN_HINT if state.get("last_error") in ("auth", "no-session", None) else "It retries by itself; `monarch-now status` says more."
        return model.broken_doc(why[0].upper() + why[1:], hint)
    cfg = load_config()
    doc = model.build_doc(snapshot, now, problem, cfg["pace_grace_days"], cfg["settings"])
    if state.get("last_error") in ("auth", "no-session"):
        doc["card"]["hint_text"] = SIGN_IN_HINT
        doc["tooltip"] = model.tooltip_text(doc["card"], doc["face"]["state"])
    return doc


def ago(seconds):
    word = fmt.age_text(seconds)
    return word if word == "just now" else word + " ago"


def cmd_status(now):
    snapshot = store.read_snapshot()
    state = store.read_state()
    try:
        session = "present" if secret.load() else "absent"
    except secret.KeyringError as e:
        session = "keyring error (%s)" % e
    lines = ["session        %s" % session]
    if snapshot:
        age = now.timestamp() - snapshot["fetched_at"]
        lines.append("last good data %s (%s)" % (ago(age), datetime.datetime.fromtimestamp(snapshot["fetched_at"], now.tzinfo).strftime("%Y-%m-%d %H:%M")))
        lines.append("window         %s to %s" % (snapshot["window_start"], snapshot["window_end"]))
        lines.append("counts         %d transactions, %d accounts, %d upcoming recurring" % (
            len(snapshot["transactions"]), len(snapshot["accounts"]), len(snapshot["recurring"])))
        cfg = load_config()
        grace, settings = cfg["pace_grace_days"], cfg["settings"]
        base = model.baseline_months(snapshot, now.date(), since=settings.usual_since)
        lines.append("usual month    %d complete months%s%s" % (
            len(base), (" (" + fmt.month_span(base[0], base[-1]) + ")") if base else "",
            ", none before %d-%02d (usual_since)" % settings.usual_since if settings.usual_since else ""))
        doc = model.build_doc(snapshot, now, PROBLEM.get(state.get("last_error") or "", ""), grace, settings)
        lines.append("face           %s" % doc["face"]["state"])
        lines.append("pace grace     %s (pace_grace_days in %s)" % (
            "off: color from day 1" if grace == 0 else "days 1-%d neutral, color from day %d" % (grace, grace + 1), store.config_path()))
        lines.append("left out       one-off tag %s; other excluded tags %s" % (
            '"%s"' % settings.one_off_tag if settings.one_off_tag else "none",
            ", ".join('"%s"' % t for t in settings.excluded_tags) or "none"))
        lines.append("groups         %d: %s" % (len(settings.grouping.order), ", ".join(settings.grouping.order)))
        if cfg["problem"]:
            lines.append("config         %s" % cfg["problem"])
        if doc["card"].get("unmapped"):
            lines.append("unmapped       %d categories counted under %s: %s" % (
                len(doc["card"]["unmapped"]), settings.grouping.catch_all, ", ".join(doc["card"]["unmapped"])))
    else:
        lines.append("last good data none")
        lines.append("face           broken")
    if state.get("last_attempt"):
        lines.append("last attempt   %s" % ago(now.timestamp() - state["last_attempt"]))
    detail = state.get("last_error_detail")
    lines.append("last error     %s" % ((state["last_error"] + (" (%s)" % detail if detail else "")) if state.get("last_error") else "none"))
    lines.append("source         %s" % ((snapshot or {}).get("source") or adapter.SOURCE))
    lines.append("files          %s (0600), log %s/log" % (store.snapshot_path(), store.state_dir()))
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def cmd_check(args, now):
    """Spending over a range by this engine's rule, with what was left out and why, to set beside
    a Monarch report that uses the same filters (expense categories, the same tags excluded).
    Prints dollar figures: for a terminal, not a log."""
    snapshot = store.read_snapshot()
    if not snapshot:
        sys.stdout.write("monarch-now check: no cached data yet\n")
        return 1
    start = model.parse_date(args[0]) if len(args) > 0 else datetime.date(now.year, 1, 1)
    end = model.parse_date(args[1]) if len(args) > 1 else now.date()
    if start < model.parse_date(snapshot["window_start"]):
        sys.stdout.write("monarch-now check: the cache starts %s, after %s\n" % (snapshot["window_start"], start))
        return 1
    settings = load_config()["settings"]
    hidden = {a["id"] for a in snapshot["accounts"] if a.get("hidden")}
    in_range = [t for t in snapshot["transactions"] if start <= model.parse_date(t["date"]) <= end]
    counted = [t for t in in_range if model.is_spending(t, hidden, settings.left_out_tags)]
    rows = {"spending (this engine's rule)": counted,
            "  of which pending": [t for t in counted if t["pending"]]}
    for tag in settings.left_out_tags:
        rows['tagged "%s", left out' % tag] = [t for t in in_range if model.is_excluded_one_off(t, hidden, (tag,))]
    rows.update({
        "hidden from reports, left out": [t for t in in_range if t["group_type"] == "expense" and (t["hidden"] or t["account_id"] in hidden)],
        "transfer-type groups, left out": [t for t in in_range if t["group_type"] == "transfer"],
        "income-type groups, left out": [t for t in in_range if t["group_type"] == "income"],
        "no category group type (!)": [t for t in in_range if t["group_type"] not in ("expense", "transfer", "income")]})
    sys.stdout.write("%s to %s, from data fetched %s\n" % (start, end, datetime.datetime.fromtimestamp(snapshot["fetched_at"], now.tzinfo).strftime("%Y-%m-%d %H:%M")))
    for label, ts in rows.items():
        total = sum((-model.D(t["amount"]) for t in ts), model.ZERO)
        sys.stdout.write("%-36s %6d transactions  %s\n" % (label, len(ts), fmt.cents(total)))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    now = datetime.datetime.now().astimezone()      # the system's own time zone
    if argv[:1] == ["login"]:
        from . import login
        return login.paste_login() if "--paste" in argv else login.browser_login()
    if argv[:1] == ["logout"]:
        secret.clear()
        state = store.read_state()
        state["last_error"] = "no-session"
        store.write_state(state)
        store.log("logout")
        sys.stdout.write("The session was removed from the keyring. (Monarch's side ends when it expires.)\n")
        return 0
    if argv[:1] == ["status"]:
        return cmd_status(now)
    if argv[:1] == ["check"]:
        return cmd_check(argv[1:], now)
    if "--as-of" in argv or "--snapshot" in argv:
        if "--as-of" in argv:
            pinned = datetime.datetime.fromisoformat(argv[argv.index("--as-of") + 1])
            now = pinned if pinned.tzinfo else pinned.astimezone()
        snapshot = store.read_json(argv[argv.index("--snapshot") + 1]) if "--snapshot" in argv else store.read_snapshot()
        if not snapshot:
            doc = model.broken_doc("No snapshot to read", "")
        else:
            cfg = load_config()
            doc = model.build_doc(snapshot, now, "", cfg["pace_grace_days"], cfg["settings"])
        sys.stdout.write(json.dumps(doc) + "\n")
        return 0
    doc = tick(now, force="--refresh" in argv)
    sys.stdout.write(json.dumps(doc) + "\n")
    return 0


VERBS = ("login", "logout", "status", "check")


def run():
    """Entry point. On the bar tick an exception prints NOTHING: the widget reads silence as a
    broken feed. A verb typed in a terminal says what went wrong (the error's class, no data)."""
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:      # noqa: BLE001
        store.log("crash", error=type(e).__name__)
        if sys.argv[1:2] and sys.argv[1] in VERBS:
            sys.stderr.write("monarch-now %s failed: %s\n" % (sys.argv[1], type(e).__name__))
            sys.exit(1)
        sys.exit(0)


if __name__ == "__main__":
    run()
