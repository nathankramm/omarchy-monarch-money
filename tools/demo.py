#!/usr/bin/env python3
"""Pupa without a Monarch account: the engine's one JSON line for an INVENTED household.

    tools/demo.py [SCENE]

    under        (default) day 12, spending under the usual pace: a green butterfly
    over         day 12 with one large purchase: an amber butterfly
    grace        day 2: the grace period, a neutral butterfly, the card still has its figures
    stale        data four hours old: a greyed butterfly and a banner
    reconnect    an account that needs reconnecting in Monarch, and day-old bank data
    new          no complete month yet: a neutral butterfly, "no usual month yet"
    signed-out   no session: the broken-link glyph and the sign-in hint

Every merchant, bill, account and amount comes from the test suite's synthetic fixture
(engine/tests/t9_monarch_model.py), computed by the real model on a pinned clock (Oct 2025), so
the output never changes. Nothing is read from Monarch, the keyring or the cache.

Show a scene on the bar, for this shell session only:
    omarchy-shell pupa.monarch fixture "python3 ~/.config/omarchy/plugins/pupa.monarch/tools/demo.py over"
    omarchy-shell pupa.monarch fixture off        # back to your own data

It needs nothing but Python 3.11+: the model is pure standard library.
"""
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "engine", "tests"))
sys.dont_write_bytecode = True

import t9_monarch_model as fixture   # noqa: E402  (the synthetic household)
from monarchnow import cli, model    # noqa: E402

SCENES = ("under", "over", "grace", "stale", "reconnect", "new", "signed-out")


def scene(name):
    if name == "signed-out":
        return model.broken_doc("Not signed in", cli.SIGN_IN_HINT)
    day, month, hour = {"grace": (2, 10, 8), "new": (20, 1, 9)}.get(name, (12, 10, 14))
    now = datetime.datetime(2025, month, day, hour, 0, tzinfo=fixture.TZ)
    age = 4 * 3600 + 300 if name == "stale" else 1800
    snap = fixture.make_snapshot((2025, 1), now.date(), datetime.date(2024, 9, 1), now.timestamp() - age)
    if name != "reconnect":
        # the fixture's second account is a day behind and wants a reconnect: a scene of its own
        for a in snap["accounts"]:
            if a["id"] == "a2":
                a["needs_reconnect"] = False
                a["updated_at"] = fixture.iso(snap["fetched_at"] - 3 * 3600)
    if name == "over":
        row = dict(snap["transactions"][0])
        row.update(id="demo-big", date=now.date().isoformat(), amount="-620.00", category_id="c-shop", category="Shopping",
                   group="Shopping", group_type="expense", tags=[], pending=False, hidden=False, account_id="a1")
        snap["transactions"].append(row)
    return model.build_doc(snap, now, "Monarch could not be reached" if name == "stale" else "")


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "under"
    if name not in SCENES:
        sys.stderr.write("demo.py: unknown scene %r (one of: %s)\n" % (name, ", ".join(SCENES)))
        return 2
    sys.stdout.write(json.dumps(scene(name)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
