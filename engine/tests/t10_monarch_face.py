#!/usr/bin/env python3
# T10: the bar tick end to end — face states, the fetch gates, the files, the silence.
#
#   t10_monarch_face.py <engine dir> [<launcher>]
#
# The real cli.tick() runs with its cache and state under a temp XDG dir, a pinned clock, and two
# seams replaced on the modules: adapter.fetch_snapshot (no network) and secret.load (no keyring).
# The snapshots come from T9's synthetic fixture (invented merchants, bills, accounts and amounts;
# a pinned clock, Oct 12, 2025). Nothing real is read or written.
#
# What it holds:
#   face states   live (good/over), plain, stale, broken (no session; refused session; no data;
#                 data too old; unreadable keyring; corrupt cache), and EMPTY OUTPUT: a crash
#                 inside the tick prints nothing at all, and so does a launcher without its venv
#   never plausible  the butterfly is on stdout only for live and stale; every broken path carries
#                 the broken glyph or nothing
#   fetch gates   at most hourly; a failure retried after 10 min, a refused session after 6 h;
#                 --refresh fetches now but not twice within 60 s; a held lock means no fetch
#   files         cache dir 0700, snapshot/state/lock/log 0600
#   log           no dollar sign, no amount, no category or account name, no cookie text
#   wording       through the real tick: the headline reads "over/under a usual day N" with N =
#                 today, never "a usual month"; "on pace for $X · usual $Y" is unchanged; every
#                 chip is one of the rows (same name, same figure, same direction); no "colour"
#   grace period  on days 1..pace_grace_days (config, default 3) the face is plain whatever the
#                 pace, while the card keeps its numbers and its tint; day N+1 is tinted; 0 turns
#                 it off; a bad config value is not used; stale data on day 1 is still stale
#   config        the real config.toml through the real tick: one_off_tag, excluded_tags and
#                 [[groups]] change the card exactly as the oracle says; a bad value leaves that
#                 key's default in force and is reported; config.example.toml is the defaults
# Then MUTANTS of cli.py / store.py must each fail at least one check.
import datetime
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
TZ = ZoneInfo("America/New_York")       # the suite's own pinned zone
COOKIES = {"session_id": "SESSION-SECRET-VALUE-zzz", "csrftoken": "CSRF-SECRET-VALUE-zzz"}


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def check(engine_dir, launcher):
    sys.path.insert(0, HERE)
    import t9_monarch_model as t9
    sys.path.insert(0, engine_dir)
    fails = []

    def need(ok, what):
        if not ok:
            fails.append(what)

    class World:
        """A fresh engine with empty dirs; fetch and keyring seams counted and steerable."""

        def __init__(self):
            self.tmp = tempfile.mkdtemp(prefix="t10-monarch-")
            os.environ["XDG_CACHE_HOME"] = os.path.join(self.tmp, "cache")
            os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "state")
            os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "config")
            for name in [n for n in sys.modules if n == "monarchnow" or n.startswith("monarchnow.")]:
                del sys.modules[name]
            self.cli = importlib.import_module("monarchnow.cli")
            self.adapter = importlib.import_module("monarchnow.adapter")
            self.secret = importlib.import_module("monarchnow.secret")
            self.store = importlib.import_module("monarchnow.store")
            self.model = importlib.import_module("monarchnow.model")
            self.fetches = 0
            self.lookups = 0
            self.session = dict(COOKIES)
            self.fail = None            # None, or a FetchError kind
            self.big_today = False      # add one large purchase dated today: an OVER month
            self.keyring_broken = False
            self.adapter.fetch_snapshot = self._fetch
            self.secret.load = self._load

        def _load(self):
            self.lookups += 1
            if self.keyring_broken:
                raise self.secret.KeyringError("locked")
            return self.session

        def _fetch(self, cookies, start, end, today):
            self.fetches += 1
            if self.fail:
                raise self.adapter.FetchError(self.fail, "HTTP 401" if self.fail == "auth" else "ClientConnectorError")
            snap = t9.make_snapshot((2025, 1), today, start, self.now.timestamp())
            if self.big_today:
                row = dict(snap["transactions"][0])
                row.update(id="tBIG", date=today.isoformat(), amount="-2000.00", category_id="c-shop", category="Shopping",
                           group="Shopping", group_type="expense", tags=[], pending=False, hidden=False, account_id="a1")
                snap["transactions"].append(row)
            return snap

        def config(self, text):
            """Write the engine's config file as given (a string), or remove it (None)."""
            path = os.path.join(self.tmp, "config", "monarch-now", "config.toml")
            if text is None:
                if os.path.exists(path):
                    os.unlink(path)
                return
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)

        def tick(self, now, force=False):
            self.now = now
            return self.cli.tick(now, force=force)

        def close(self):
            shutil.rmtree(self.tmp, ignore_errors=True)

    def face(doc):
        return doc["face"]["state"]

    t0 = datetime.datetime(2025, 10, 12, 14, 0, tzinfo=TZ)

    def later(**kw):
        return t0 + datetime.timedelta(**kw)

    # ---- 1. live: a first tick fetches, paints a butterfly, and writes private files
    w = World()
    doc = w.tick(t0)
    need(w.fetches == 1, "a cold start must fetch once (fetched %d)" % w.fetches)
    need(face(doc) in ("good", "over") and doc["text"] == w.model.GLYPH, "live: face %r" % face(doc))
    need(set(doc) == {"v", "text", "tooltip", "face", "card"}, "the document's keys: %r" % sorted(doc))
    line = json.dumps(doc)
    need("\n" not in line and json.loads(line) == doc, "the document must be one JSON line")
    cache = os.path.join(w.tmp, "cache", "monarch-now")
    need(mode(cache) == 0o700, "cache dir mode %o" % mode(cache))
    for f in ("snapshot.json", "state.json", "lock"):
        need(mode(os.path.join(cache, f)) == 0o600, "%s mode %o" % (f, mode(os.path.join(cache, f))))
    log_path = os.path.join(w.tmp, "state", "monarch-now", "log")
    need(mode(log_path) == 0o600, "log mode %o" % mode(log_path))

    # ---- 2. the hourly gate
    w.tick(later(minutes=10))
    w.tick(later(minutes=59))
    need(w.fetches == 1, "no second fetch inside the hour (fetched %d)" % w.fetches)
    doc = w.tick(later(minutes=61))
    need(w.fetches == 2, "a fetch after the hour (fetched %d)" % w.fetches)
    # ---- 3. --refresh: now, but with a floor
    w.tick(later(minutes=70), force=True)
    need(w.fetches == 3, "--refresh must fetch inside the hour (fetched %d)" % w.fetches)
    w.tick(later(minutes=70, seconds=30), force=True)
    need(w.fetches == 3, "--refresh twice within 60 s must not fetch twice (fetched %d)" % w.fetches)
    # ---- 4. a held lock: serve the cache, do not fetch
    import fcntl
    fh = open(os.path.join(cache, "lock"), "w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    doc = w.tick(later(hours=3), force=True)
    need(w.fetches == 3 and face(doc) in ("good", "over"), "a held lock must mean no fetch and the cache served")
    fcntl.flock(fh, fcntl.LOCK_UN)
    fh.close()
    # ---- 5. network failure: the cache carries on, says so, goes stale, then broken
    w.fail = "network"
    base = later(minutes=70)                       # the last good fetch
    doc = w.tick(base + datetime.timedelta(minutes=61))
    need(w.fetches == 4 and face(doc) in ("good", "over") and "Last check failed (Monarch could not be reached)" in doc["card"]["banner_text"], "a failed check behind fresh data: %r / %r" % (face(doc), doc["card"]["banner_text"]))
    w.tick(base + datetime.timedelta(minutes=66))
    need(w.fetches == 4, "a failed check must not be retried inside 10 minutes")
    w.tick(base + datetime.timedelta(minutes=72))
    need(w.fetches == 5, "a failed check must be retried after 10 minutes")
    doc = w.tick(base + datetime.timedelta(hours=3, minutes=5))
    need(face(doc) == "stale" and doc["text"] == w.model.GLYPH and "could not be reached" in doc["card"]["banner_text"], "3 h without data must be stale and say why: %r" % face(doc))
    doc = w.tick(base + datetime.timedelta(hours=36, minutes=5))
    need(face(doc) == "broken" and doc["text"] == w.model.BROKEN_GLYPH and doc["card"]["hero"] is None, "36 h without data must be broken: %r" % face(doc))
    # ---- 6. the log: counts and classes only
    log = open(log_path, encoding="utf-8").read()
    need(log.count("fetch result=ok") == 3 and "fetch result=network" in log, "the log must record each fetch")
    snap = w.store.read_snapshot()
    leaks = [t["amount"].lstrip("-") for t in snap["transactions"][:400] if len(t["amount"]) > 5 and t["amount"].lstrip("-") in log]
    names = [n for n in ("Groceries", "Shopping", "Maple Card", "Birch Checking", "Northlight", "Fernhill", "SESSION-SECRET", "CSRF-SECRET") if n in log]
    need("$" not in log and not leaks and not names, "the log leaks: dollar=%s amounts=%d names=%r" % ("$" in log, len(leaks), names))
    w.close()

    # ---- 7. no session: broken, with the sign-in hint, and no hammering of the keyring
    w = World()
    w.session = None
    doc = w.tick(t0)
    need(face(doc) == "broken" and doc["text"] == w.model.BROKEN_GLYPH, "no session must be broken")
    need("Not signed in" in doc["card"]["banner_text"] and "monarch-now login" in doc["card"]["hint_text"] and "monarch-now login" in doc["tooltip"], "no session must say how to sign in")
    w.tick(later(minutes=5))
    w.tick(later(hours=5))
    need(w.lookups == 1 and w.fetches == 0, "no session: the keyring must not be asked every tick (asked %d)" % w.lookups)
    # after a login the state is cleared (login.py does this) and the next tick fetches at once
    w.session = dict(COOKIES)
    st = w.store.read_state()
    st.pop("last_error", None)
    st.pop("last_attempt", None)
    w.store.write_state(st)
    doc = w.tick(later(hours=5, minutes=1))
    need(w.fetches == 1 and face(doc) in ("good", "over"), "after a sign-in the next tick must fetch and go live")
    # ---- 8. a refused session: still live while fresh, hint shown, not retried for 6 h
    w.fail = "auth"
    b = later(hours=5, minutes=1)
    doc = w.tick(b + datetime.timedelta(minutes=61))
    need(w.fetches == 2 and "signed this session out" in doc["card"]["banner_text"] and "monarch-now login" in doc["card"]["hint_text"], "a refused session must be said, with the hint")
    w.tick(b + datetime.timedelta(hours=3))
    w.tick(b + datetime.timedelta(hours=6))
    need(w.fetches == 2, "a refused session must not be retried inside 6 h (fetched %d)" % w.fetches)
    w.tick(b + datetime.timedelta(hours=7, minutes=10))
    need(w.fetches == 3, "a refused session is retried after 6 h")
    w.close()

    # ---- 9. a refused session with no data at all, an unreadable keyring, a corrupt cache
    w = World()
    w.fail = "auth"
    doc = w.tick(t0)
    need(face(doc) == "broken" and "signed this session out" in doc["card"]["banner_text"] and "monarch-now login" in doc["card"]["hint_text"], "refused with no data must be broken with the hint")
    w.close()
    w = World()
    w.keyring_broken = True
    doc = w.tick(t0)
    need(face(doc) == "broken" and "keyring" in doc["card"]["banner_text"], "an unreadable keyring must be broken and named")
    w.close()
    w = World()
    w.tick(t0)
    with open(os.path.join(w.tmp, "cache", "monarch-now", "snapshot.json"), "w") as fh:
        fh.write('{"fetched_at": "yesterday", "transactions": 7')
    w.fail = "network"
    doc = w.tick(later(minutes=5))
    need(face(doc) == "broken" and doc["text"] == w.model.BROKEN_GLYPH, "a corrupt cache must be broken, never a butterfly")
    w.close()

    # ---- 12. THE GRACE PERIOD, through the real tick and the real config file
    def face_on(day, over, config_text="<none>", hour=9):
        """A fresh engine, one tick on Oct `day`: (face, card). Over = a large purchase that day."""
        w = World()
        if config_text != "<none>":
            w.config(config_text)
        w.big_today = over
        doc = w.tick(datetime.datetime(2025, 10, day, hour, 0, tzinfo=TZ))
        problem = w.cli.load_config()["problem"]
        w.close()
        return doc, problem

    for day, over, want in ((1, True, "plain"), (1, False, "plain"), (2, True, "plain"), (3, True, "plain"), (3, False, "plain"),
                            (4, True, "over"), (4, False, "good"), (12, True, "over")):
        doc, _ = face_on(day, over)
        tag = "grace default, Oct %d, %s: " % (day, "over" if over else "under")
        need(face(doc) == want, tag + "face %r, expected %r" % (face(doc), want))
        need(doc["text"] == chr(0xF1589), tag + "a neutral face is still the butterfly")
        hero = doc["card"]["hero"]
        need(hero["pace_dir"] == ("over" if over else "good") and "$" in hero["pace_text"] and "$" in hero["detail_text"] and doc["card"]["chart"] is not None,
             tag + "the card must keep its numbers and tint (pace_dir %r, %r)" % (hero["pace_dir"], hero["pace_text"]))
        need(("bar color from day 4" in doc["card"]["note_text"]) == (day <= 3), tag + "note %r" % doc["card"]["note_text"])
    # the config value is what decides
    for text, day, want, label in (('pace_grace_days = 0', 1, "over", "0 = color from day 1"),
                                   ('pace_grace_days = 5', 5, "plain", "5: day 5 neutral"),
                                   ('pace_grace_days = 5', 6, "over", "5: day 6 tinted"),
                                   ('pace_grace_days = 1', 2, "over", "1: day 2 tinted"),
                                   ('pace_grace_days = 1', 1, "plain", "1: day 1 neutral")):
        doc, problem = face_on(day, True, text)
        need(face(doc) == want and problem == "", "grace config %s: face %r, expected %r (problem %r)" % (label, face(doc), want, problem))
    doc, _ = face_on(5, True, 'pace_grace_days = 5')
    need("bar color from day 6" in doc["card"]["note_text"], "grace 5: the note must name day 6")
    # a bad value is never used: the default stands (day 3 neutral, day 4 and day 12 tinted) and status can say why
    # out of range twice, a string, a boolean, a fraction, a list, a date, and a file that is not TOML
    for text in ('pace_grace_days = 99', 'pace_grace_days = -1', 'pace_grace_days = "3"', 'pace_grace_days = true',
                 'pace_grace_days = 2.5', 'pace_grace_days = [3]', 'pace_grace_days = 2025-01-01', 'pace_grace_days = '):
        d3, problem = face_on(3, True, text)
        d4, _ = face_on(4, True, text)
        d12, _ = face_on(12, True, text)
        need(face(d3) == "plain" and face(d4) == "over" and face(d12) == "over" and problem != "",
             "bad config %s: day 3 %r, day 4 %r, day 12 %r, problem %r" % (text, face(d3), face(d4), face(d12), problem))
    # grace never hides age: no fresh data for three hours on day 1 is STALE, not neutral
    w = World()
    w.big_today = True
    first = datetime.datetime(2025, 10, 1, 8, 0, tzinfo=TZ)
    need(face(w.tick(first)) == "plain", "grace: day 1 08:00 must be plain")
    w.fail = "network"
    doc = w.tick(first + datetime.timedelta(hours=3, minutes=5))
    need(face(doc) == "stale", "grace must not hide stale data on day 1: %r" % face(doc))
    w.close()

    # ---- 13. WORDING, through the real tick (T9's two rules, applied to what the bar receives)
    for day, over in ((1, True), (1, False), (4, True), (4, False), (12, True), (12, False), (31, True)):
        doc, _ = face_on(day, over)
        tag = "wording, Oct %d, %s: " % (day, "over" if over else "under")
        for f in t9.wording_rules(json.loads(json.dumps(doc)), datetime.datetime(2025, 10, day, 9, 0, tzinfo=TZ)):
            fails.append(tag + f)
        want = "over a usual day %d" % day if over else "under a usual day %d" % day
        need(doc["card"]["hero"]["pace_text"].endswith(want), tag + "headline %r must end %r" % (doc["card"]["hero"]["pace_text"], want))
        need(want in doc["tooltip"], tag + "the text fallback must carry the same headline")
        names = [g["name"] for g in doc["card"]["groups"]]
        need(len(doc["card"]["chips"]) >= 1 and all(c["label"] in names for c in doc["card"]["chips"]), tag + "chips %r must be rows" % [c["label"] for c in doc["card"]["chips"]])
        need(all(("%s %s" % (c["label"], c["value_text"])) in doc["tooltip"] for c in doc["card"]["chips"]), tag + "the text fallback must carry the same chips")

    # ---- 14. THE CONFIG FILE's other keys, through the real tick: the tags and the groups
    def tick_with(config_text, day=12):
        """A fresh engine, the given config.toml (None = no file), one tick: (doc, config, snapshot, now)."""
        w = World()
        if config_text is not None:
            w.config(config_text)
        now = datetime.datetime(2025, 10, day, 9, 0, tzinfo=TZ)
        doc = json.loads(json.dumps(w.tick(now)))
        cfg, snap = w.cli.load_config(), w.store.read_snapshot()
        w.close()
        return doc, cfg, snap, now

    def like_oracle(label, doc, snap, now, **oracle_kw):
        want, _ = t9.Oracle(snap, now, **oracle_kw).card()
        for d_ in t9.diff(doc["card"], want, "card")[:4]:
            fails.append("config/%s (oracle): %s" % (label, d_))

    base_doc, base_cfg, snap, now = tick_with(None)
    need(base_cfg["problem"] == "" and [g["name"] for g in base_doc["card"]["groups"]] == t9.ORDER, "no config file: the default groups and no complaint")
    like_oracle("no file", base_doc, snap, now)
    doc, cfg, snap, now = tick_with('excluded_tags = ["Work trip"]\n')
    need(cfg["problem"] == "" and doc["card"]["hero"]["total_text"] != base_doc["card"]["hero"]["total_text"], "excluded_tags must reach the card (problem %r)" % cfg["problem"])
    like_oracle("excluded_tags", doc, snap, now, excluded=("Work trip",))
    doc, cfg, snap, now = tick_with('one_off_tag = "Big buy"\n')
    need(cfg["problem"] == "" and doc["card"]["note_text"].startswith("one-offs excluded"), "one_off_tag must reach the card (problem %r)" % cfg["problem"])
    like_oracle("one_off_tag", doc, snap, now, one_off="Big buy")
    doc, cfg, snap, now = tick_with('one_off_tag = ""\n')
    need(cfg["problem"] == "" and "one-off" not in doc["card"]["note_text"], 'one_off_tag = "" must mean no one-off tag: %r' % doc["card"]["note_text"])
    like_oracle("no one-off tag", doc, snap, now, one_off="")
    doc, cfg, snap, now = tick_with('usual_since = "2025-04"\n')
    need(cfg["problem"] == "" and "usual = Apr–Sep 2025 average" in doc["card"]["note_text"], "usual_since must reach the card (problem %r, note %r)" % (cfg["problem"], doc["card"]["note_text"]))
    like_oracle("usual_since", doc, snap, now, since=(2025, 4))
    own = ('[[groups]]\nname = "Eating"\ncategories = ["Groceries", "Restaurants & Bars"]\n\n'
           '[[groups]]\nname = "Wheels"\ncategories = ["Gas"]\n\n[[groups]]\nname = "Everything else"\n')
    doc, cfg, snap, now = tick_with(own)
    need(cfg["problem"] == "" and [g["name"] for g in doc["card"]["groups"]] == ["Eating", "Wheels", "Everything else"], "[[groups]] must be the card's rows (problem %r)" % cfg["problem"])
    need(all(c["label"] in ("Eating", "Wheels", "Everything else") for c in doc["card"]["chips"]) and "Eating" in doc["tooltip"], "configured groups must name the chips and the text fallback too")
    like_oracle("[[groups]]", doc, snap, now, order=["Eating", "Wheels", "Everything else"],
                group_of={"Groceries": "Eating", "Restaurants & Bars": "Eating", "Gas": "Wheels"})
    # a bad value is never used: that key's default stays in force, and the config says why
    for label, text, word in (("one_off_tag not a string", "one_off_tag = 7\n", "one_off_tag"),
                              ("excluded_tags not a list", 'excluded_tags = "Work trip"\n', "excluded_tags"),
                              ("excluded_tags with a blank", 'excluded_tags = ["Work trip", " "]\n', "excluded_tags"),
                              ("a category in two groups", '[[groups]]\nname = "A"\ncategories = ["Gas"]\n[[groups]]\nname = "B"\ncategories = ["Gas"]\n', "groups"),
                              ("nine groups", "".join('[[groups]]\nname = "G%d"\n' % i for i in range(9)), "groups"),
                              ("groups not tables", 'groups = ["Food", "Other"]\n', "groups"),
                              ("usual_since in words", 'usual_since = "April 2025"\n', "usual_since"),
                              ("usual_since month 13", 'usual_since = "2025-13"\n', "usual_since"),
                              ("usual_since without the leading zero", 'usual_since = "2025-4"\n', "usual_since"),
                              ("usual_since as a number", "usual_since = 202504\n", "usual_since"),
                              ("usual_since as a full date", "usual_since = 2025-04-01\n", "usual_since"),
                              ("usual_since with a year that does not exist", 'usual_since = "0000-04"\n', "usual_since"),
                              ("a misspelt key", "pace_grace_day = 5\n", "pace_grace_day")):
        doc, cfg, snap, now = tick_with(text)
        need(word in cfg["problem"] and doc["card"] == base_doc["card"], "bad config, %s: the defaults must stay in force and the problem be named (%r)" % (label, cfg["problem"]))
    # one bad key does not take the good ones down with it, and both complaints are kept
    doc, cfg, snap, now = tick_with('pace_grace_days = 99\nexcluded_tags = ["Work trip"]\none_off_tag = 7\n')
    need(cfg["pace_grace_days"] == 3 and cfg["settings"].excluded_tags == ("Work trip",) and "pace_grace_days" in cfg["problem"] and "one_off_tag" in cfg["problem"],
         "a bad key beside good ones: %r" % cfg["problem"])
    like_oracle("a bad key beside good ones", doc, snap, now, excluded=("Work trip",))
    # `monarch-now status` names the months the usual month uses, and says why a value is not in use
    def status_with(config_text):
        import io
        w = World()
        w.config(config_text)
        now = datetime.datetime(2025, 10, 12, 9, 0, tzinfo=TZ)
        w.tick(now)
        buf, old = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            w.cli.cmd_status(now)
        finally:
            sys.stdout = old
        w.close()
        return buf.getvalue()

    out = status_with('usual_since = "2025-04"\n')
    need("6 complete months (Apr–Sep 2025), none before 2025-04 (usual_since)" in out and "\nconfig " not in out, "status with a good usual_since: %r" % out)
    out = status_with('usual_since = "2025-4"\n')
    need("9 complete months (Jan–Sep 2025)" in out and "none before" not in out and "\nconfig " in out and "usual_since must be a month" in out,
         "status with a bad usual_since must show the default in force and say why: %r" % out)
    need("$" not in out, "status must print no amounts")
    # config.example.toml: as shipped, and with its [[groups]] block uncommented, it IS the defaults
    example = os.path.join(HERE, "..", "..", "config.example.toml")
    need(os.path.exists(example), "config.example.toml must sit at the top of the tree")
    if os.path.exists(example):
        text = open(example, encoding="utf-8").read()
        doc, cfg, snap, now = tick_with(text)
        need(cfg["problem"] == "" and doc["card"] == base_doc["card"] and cfg["pace_grace_days"] == 3, "config.example.toml as shipped must be the defaults (%r)" % cfg["problem"])
        head, _, rest = text.partition("# The defaults, written out")
        block, _, tail = rest.partition("# ---- Not settings")
        opened = "\n".join(line[2:] if line.startswith("# ") else ("" if line == "#" else line) for line in block.split("\n")[1:])
        doc, cfg, snap, now = tick_with(head + opened + "\n# ---- Not settings" + tail)
        need(cfg["problem"] == "" and cfg["settings"].grouping.order == t9.ORDER and doc["card"] == base_doc["card"],
             "config.example.toml's [[groups]] block, uncommented, must equal the built-in defaults (%r)" % cfg["problem"])
        need(text.count("[[groups]]") >= 9 and "pace_grace_days = 3" in text and 'one_off_tag = "One-off"' in text and "excluded_tags = []" in text
             and '\n# usual_since = "' in text and cfg["settings"].usual_since is None,
             "config.example.toml must show every key with its default")

    # ---- 10. plain: signed in, data fresh, no complete month yet
    w = World()
    jan = datetime.datetime(2025, 1, 20, 9, 0, tzinfo=TZ)
    doc = w.tick(jan)
    need(face(doc) == "plain" and doc["text"] == w.model.GLYPH, "no usual month yet must be the plain face: %r" % face(doc))
    # the window: the twelve months the usual month can use, plus one lead month, whatever the year
    need(w.cli.fetch_window(jan.date()) == (datetime.date(2023, 12, 1), jan.date()), "fetch window in January")
    need(w.cli.fetch_window(t0.date()) == (datetime.date(2024, 9, 1), t0.date()), "fetch window in October")
    need(w.cli.fetch_window(datetime.date(2026, 3, 15)) == (datetime.date(2025, 2, 1), datetime.date(2026, 3, 15)), "fetch window is twelve complete months and a lead month")
    # ---- 11. EMPTY OUTPUT: a crash inside the tick prints nothing
    w.model.build_doc = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    w.cli.model = w.model
    import io
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr, sys.argv
    sys.stdout, sys.stderr, sys.argv = out, err, ["monarch-now"]
    try:
        try:
            w.cli.run()
            rc = "returned"
        except SystemExit as e:
            rc = e.code
    finally:
        sys.stdout, sys.stderr, sys.argv = old
    need(out.getvalue() == "" and rc == 0, "a crash on the tick must print nothing and exit 0 (stdout %r, rc %r)" % (out.getvalue()[:40], rc))
    w.close()
    if launcher:
        empty = tempfile.mkdtemp(prefix="t10-nohome-")
        p = subprocess.run(["bash", launcher], capture_output=True, text=True, timeout=30, env=dict(os.environ, MONARCH_NOW_HOME=empty))
        need(p.stdout == "" and p.returncode != 0, "a launcher without its venv must print nothing on stdout")
        shutil.rmtree(empty, ignore_errors=True)
    return fails


MUTANTS = [
    ("fetch every minute instead of hourly", "cli.py", "FETCH_INTERVAL = 3600 ", "FETCH_INTERVAL = 60   "),
    ("failures retried on every tick", "cli.py", "RETRY_AFTER_FAILURE = 600 ", "RETRY_AFTER_FAILURE = 0   "),
    ("a refused session retried every ten minutes", "cli.py", '    if state.get("last_error") == "auth" or state.get("last_error") == "no-session":\n        return since >= RETRY_AFTER_AUTH', '    if False:\n        return since >= RETRY_AFTER_AUTH'),
    ("--refresh without a floor", "cli.py", "        return since >= REFRESH_FLOOR", "        return True"),
    ("--refresh ignored", "cli.py", "    if force:\n        return since >= REFRESH_FLOOR\n", ""),
    ("the lock is ignored", "cli.py", "            if held:\n                fresh = try_fetch(state, now)", "            if True:\n                fresh = try_fetch(state, now)"),
    ("no data paints an empty live card", "cli.py", "    if snapshot is None:\n        why = problem", "    if False:\n        why = problem"),
    ("the failure is not said on the card", "cli.py", '    doc = model.build_doc(snapshot, now, problem, cfg["pace_grace_days"], cfg["settings"])', '    doc = model.build_doc(snapshot, now, "", cfg["pace_grace_days"], cfg["settings"])'),
    ("no sign-in hint", "cli.py", '        doc["card"]["hint_text"] = SIGN_IN_HINT\n', '        pass\n'),
    ("a crash prints a traceback on the bar", "cli.py", '        if sys.argv[1:2] and sys.argv[1] in VERBS:', '        sys.stdout.write("Traceback: %s\\n" % e)\n        if sys.argv[1:2] and sys.argv[1] in VERBS:'),
    ("grace: ignored, the face is tinted on day 1", "model.py", "    if day is not None and day <= grace_days:\n        return \"plain\"", "    if False:\n        return \"plain\""),
    ("grace: off by one, day 3 is already tinted", "model.py", "    if day is not None and day <= grace_days:\n        return \"plain\"", "    if day is not None and day < grace_days:\n        return \"plain\""),
    ("grace: one day too long, day 4 still neutral", "model.py", "    if day is not None and day <= grace_days:\n        return \"plain\"", "    if day is not None and day <= grace_days + 1:\n        return \"plain\""),
    ("grace: hides stale data", "model.py", "    if age > STALE_AFTER:\n        return \"stale\"\n    if day is not None and day <= grace_days:\n        return \"plain\"", "    if day is not None and day <= grace_days:\n        return \"plain\"\n    if age > STALE_AFTER:\n        return \"stale\""),
    ("grace: the card loses its tint too", "model.py", '        hero["pace_dir"] = "over" if pace_state == "over" else "good"', '        hero["pace_dir"] = "none" if d <= grace_days else ("over" if pace_state == "over" else "good")'),
    ("grace: the default is zero days", "model.py", "PACE_GRACE_DAYS = 3 ", "PACE_GRACE_DAYS = 0 "),
    ("grace: the tick ignores the config value", "cli.py", '    doc = model.build_doc(snapshot, now, problem, cfg["pace_grace_days"], cfg["settings"])', '    doc = model.build_doc(snapshot, now, problem, model.PACE_GRACE_DAYS, cfg["settings"])'),
    ("grace: the tick switches it off", "cli.py", '    doc = model.build_doc(snapshot, now, problem, cfg["pace_grace_days"], cfg["settings"])', '    doc = model.build_doc(snapshot, now, problem, 0, cfg["settings"])'),
    ("config: the tick ignores the tags and the groups", "cli.py", '    doc = model.build_doc(snapshot, now, problem, cfg["pace_grace_days"], cfg["settings"])', '    doc = model.build_doc(snapshot, now, problem, cfg["pace_grace_days"])'),
    ("config: excluded_tags is never read", "cli.py", '    if "excluded_tags" in doc:\n', '    if False:\n'),
    ("config: one_off_tag is never read", "cli.py", '    if "one_off_tag" in doc:\n', '    if False:\n'),
    ("config: [[groups]] is never read", "cli.py", '    if "groups" in doc:\n', '    if False:\n'),
    ("config: a bad excluded_tags value is used", "cli.py", '        if isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v):', '        if True:'),
    ("config: a bad one_off_tag is silent", "cli.py", "            more.append('one_off_tag must be a tag name in quotes; the default (\"%s\") is in use' % model.ONE_OFF_TAG)", '            pass'),
    ("config: bad groups are silent", "cli.py", '            more.append("groups: %s; the default groups are in use" % why)', '            pass'),
    ("config: a misspelt key is silent", "cli.py", '    if unknown:\n', '    if False:\n'),
    ("config: a second complaint replaces the first", "cli.py", '    cfg["problem"] = "; ".join(p for p in [cfg["problem"]] + more if p)', '    cfg["problem"] = "; ".join(more) or cfg["problem"]'),
    ("config: usual_since is never read", "cli.py", '    if "usual_since" in doc:\n', '    if False:\n'),
    ("config: a loosely written usual_since is used", "cli.py", 'USUAL_SINCE = re.compile(r"^(\\d{4})-(0[1-9]|1[0-2])$")', 'USUAL_SINCE = re.compile(r"^(\\d{4})-(0?[1-9]|1[0-2])$")'),
    ("config: a year that does not exist is used", "cli.py", "        if m and int(m.group(1)) >= 1:", "        if m:"),
    ("config: a bad usual_since is silent", "cli.py", "            more.append('usual_since must be a month written \"YYYY-MM\", in quotes; it is not in use')", '            pass'),
    ("config: usual_since is dropped on the way to the model", "cli.py", '    cfg["settings"] = model.Settings(one_off, excluded, grouping, usual_since)', '    cfg["settings"] = model.Settings(one_off, excluded, grouping)'),
    ("status hides why a config value is not in use", "cli.py", '        if cfg["problem"]:\n            lines.append("config         %s" % cfg["problem"])', '        if False:\n            lines.append("config         %s" % cfg["problem"])'),
    ("status's usual-month line ignores usual_since", "cli.py", "        base = model.baseline_months(snapshot, now.date(), since=settings.usual_since)", "        base = model.baseline_months(snapshot, now.date())"),
    ("the fetch window has no lead month", "cli.py", '    for _ in range(model.BASELINE_MAX_MONTHS + HISTORY_LEAD_MONTHS):', '    for _ in range(model.BASELINE_MAX_MONTHS):'),
    ("grace: an out-of-range config value is used", "cli.py", "        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 31:", "        if isinstance(v, int) and not isinstance(v, bool):"),
    ("grace: a non-number config value is used", "cli.py", "        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 31:", "        if True:"),
    ("grace: a bad config value is silent", "cli.py", '            cfg["problem"] = "pace_grace_days must be a whole number from 0 to 31; the default (%d) is in use" % model.PACE_GRACE_DAYS', '            pass'),
    ("wording: the over headline says a usual month", "model.py", '            hero["pace_text"] = "%s over a usual day %d" % (arrow_text(diff), d)', '            hero["pace_text"] = "%s over a usual month" % arrow_text(diff)'),
    ("wording: the under headline says a usual month", "model.py", '            hero["pace_text"] = "%s under a usual day %d" % (arrow_text(diff), d)', '            hero["pace_text"] = "%s under a usual month" % arrow_text(diff)'),
    ("wording: the headline names the wrong day", "model.py", '            hero["pace_text"] = "%s over a usual day %d" % (arrow_text(diff), d)', '            hero["pace_text"] = "%s over a usual day %d" % (arrow_text(diff), M)'),
    ("wording: a chip is named after something that is not a row", "model.py", 'chips = [{"label": row["name"], "value_text": row["vs_text"], "dir": row["dir"]}', 'chips = [{"label": row["name"] + " & more", "value_text": row["vs_text"], "dir": row["dir"]}'),
    ("wording: a chip's figure is not its row's", "model.py", 'chips = [{"label": row["name"], "value_text": row["vs_text"], "dir": row["dir"]}', 'chips = [{"label": row["name"], "value_text": row["value_text"], "dir": row["dir"]}'),
    ("wording: a chip's direction is not its row's", "model.py", 'chips = [{"label": row["name"], "value_text": row["vs_text"], "dir": row["dir"]}', 'chips = [{"label": row["name"], "value_text": row["vs_text"], "dir": "over"}'),
    ("wording: 'on pace for' reworded", "model.py", '        hero["detail_text"] = "on pace for %s %s usual %s"', '        hero["detail_text"] = "heading for %s %s usual %s"'),
    ("wording: British spelling on the card", "model.py", '            parts.append("bar color from day %d" % (grace_days + 1))', '            parts.append("bar colour from day %d" % (grace_days + 1))'),
    ("the snapshot is world-readable", "store.py", "    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\n    try:\n        with os.fdopen(fd, \"w\", encoding=\"utf-8\") as fh:\n            json.dump(doc, fh)\n        os.chmod(tmp, 0o600)", "    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)\n    try:\n        with os.fdopen(fd, \"w\", encoding=\"utf-8\") as fh:\n            json.dump(doc, fh)\n        os.chmod(tmp, 0o644)"),
    ("the cache dir is world-readable", "store.py", "    os.makedirs(path, mode=0o700, exist_ok=True)\n    os.chmod(path, 0o700)", "    os.makedirs(path, mode=0o755, exist_ok=True)\n    os.chmod(path, 0o755)"),
    ("a corrupt cache is trusted", "store.py", '    if not doc or not isinstance(doc.get("fetched_at"), (int, float)) or not isinstance(doc.get("transactions"), list):\n        return None', '    if not doc:\n        return {"fetched_at": 0, "transactions": [], "accounts": [], "recurring": [], "window_start": "2025-01-01"}'),
    ("the log records the fetched amounts", "cli.py", '    store.log("fetch", result="ok", transactions=len(snap["transactions"]),', '    store.log("fetch", result="ok", total=sum(float(t["amount"]) for t in snap["transactions"]), first=snap["transactions"][0]["amount"], transactions=len(snap["transactions"]),'),
    ("the log records the cookie", "cli.py", '    start, end = fetch_window(now.date())\n', '    start, end = fetch_window(now.date())\n    store.log("session", cookie=cookies["session_id"])\n'),
]


def main():
    if len(sys.argv) < 2:
        print("usage: t10_monarch_face.py <engine dir> [<launcher>]")
        return 2
    if sys.argv[1] == "--check":
        print(json.dumps(check(os.path.abspath(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "")))
        return 0
    engine = os.path.abspath(sys.argv[1])
    launcher = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else ""

    def run(dirpath):
        p = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--check", dirpath, launcher], capture_output=True, text=True, timeout=600)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return ["the check crashed: " + (p.stderr.strip().splitlines() or ["no output"])[-1]]

    fails = run(engine)
    print("T10 candidate: %s" % engine)
    for f in fails:
        print("  FAIL " + f)
    print("T10 candidate: %s" % ("PASS" if not fails else "%d FAILED" % len(fails)))
    rc = 1 if fails else 0
    killed = 0
    for name, fname, old, new in MUTANTS:
        tmp = tempfile.mkdtemp(prefix="t10-mutant-")
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
            if mf:
                killed += 1
                print("  mutant killed (%2d checks): %s" % (len(mf), name))
            else:
                print("  MUTANT SURVIVED: %s" % name)
                rc = 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("T10 mutants killed: %d/%d" % (killed, len(MUTANTS)))
    print("T10: %s" % ("ALL PASS" if rc == 0 else "FAILED"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
