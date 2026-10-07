#!/usr/bin/env python3
"""The hover gesture test for the Pupa bar widget, run with a virtual pointer (vpointer.py).

    gesture_test.py [--click] [--target io.github.nathankramm.pupa] [--x X --y Y]
                    [--other-x X] [--other-target PLUGIN_ID]

It MOVES THE REAL CURSOR for about twenty seconds; keep hands off the pointer. The cursor is put
back where it was. `--click` also runs the two click gestures, which open (or focus) Monarch.

The widget says where its butterfly sits (`omarchy-shell io.github.nathankramm.pupa status`: face_x, face_y),
so no coordinates are needed on a top bar; --x/--y override them. It works on any data: your
own, or the invented household (see tools/demo.py):
    omarchy-shell io.github.nathankramm.pupa fixture "python3 <this checkout>/tools/demo.py under"

Gestures, each judged from the widget's own IPC status sampled about eight times a second:
  1 rest on the butterfly        closed -> opening -> open, open within ~0.6 s
  2 move down into the card      stays open across the gap and inside the card
  3 leave the card               closing -> closed within ~0.8 s
  4 fast sweep across            never opens
  5 move to another widget       this card closes (--other-x, default a little to the left; with
                                 --other-target, a hover-card plugin there, that card must open)
  6 click "Open Monarch"         [--click] the card closes and a Monarch window exists
  7 click the butterfly          [--click] Monarch is focused (or opened); no second window

Gestures 6 and 7 count a window whose class or title names Monarch: the web app
(install-webapp.sh) or, without it, the browser tab that the fallback opens.
"""
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VP = os.path.join(HERE, "vpointer.py")


def arg(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


TARGET = arg("--target", "io.github.nathankramm.pupa")
OTHER_TARGET = arg("--other-target", "")


def status(target=TARGET):
    try:
        return json.loads(subprocess.run(["omarchy-shell", target, "status"], capture_output=True, text=True, timeout=5).stdout)
    except (ValueError, subprocess.TimeoutExpired):
        return {}


class Poller:
    def __init__(self, also=None):
        self.rows, self.also, self.stop = [], also, False
        self.t0 = time.monotonic()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop:
            s = status()
            row = [round(time.monotonic() - self.t0, 2), s.get("hover_state"), s.get("hover_open")]
            if self.also:
                row.append(status(self.also).get("hover_state"))
            self.rows.append(row)
            time.sleep(0.07)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.stop = True
        self.thread.join()

    def states(self):
        out = []
        for r in self.rows:
            if not out or out[-1] != r[1]:
                out.append(r[1])
        return out

    def first(self, state):
        return next((r[0] for r in self.rows if r[1] == state), None)


def vp(*ops):
    subprocess.run([sys.executable, VP] + list(ops), check=True)


def hypr(*args):
    return subprocess.run(["hyprctl"] + list(args), capture_output=True, text=True).stdout


def wait_for(cond, seconds=25.0):
    """Poll until cond() holds: a cold browser can take many seconds to show its window."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.4)
    return bool(cond())


def main():
    s = status()
    if not s:
        print("no answer from `omarchy-shell %s status`: is the widget on the bar?" % TARGET)
        return 2
    if s.get("hover_mode") != "card":
        print("the widget is not in card mode (hover_mode=%r)" % s.get("hover_mode"))
        return 2
    X, Y = arg("--x", int(s.get("face_x") or 0)), arg("--y", int(s.get("face_y") or 0))
    if X <= 0 or Y <= 0:
        print("the widget did not say where it is (face_x/face_y); pass --x and --y")
        return 2
    OTHER_X = arg("--other-x", X - 60)
    bar_h = 2 * Y                       # the butterfly sits on the bar's centre line
    pos = hypr("cursorpos").strip().split(",")
    mons = json.loads(hypr("monitors", "-j"))
    mon = next((m for m in mons if m.get("focused")), mons[0])
    home = (int(pos[0]) - mon["x"], int(pos[1]) - mon["y"])
    print("widget %s at %d,%d; face %s; fixture %s" % (TARGET, X, Y, s.get("face_state"), s.get("fixture")))
    card_h = 0
    results = []

    def report(n, name, ok, detail):
        results.append(ok)
        print("%s  %d %-28s %s" % ("PASS" if ok else "FAIL", n, name, detail))

    away = "glide:%d,%d,150" % (X + 420, 600)
    vp("move:%d,%d" % (X + 200, 400), "sleep:0.6")

    # 1 rest
    with Poller() as p:
        vp("glide:%d,%d,180" % (X, Y), "sleep:1.2")
    t_open = p.first("open")
    t_opening = p.first("opening")
    ok = p.states()[:3] in (["closed", "opening", "open"],) and t_open is not None and t_opening is not None and (t_open - t_opening) < 0.6
    report(1, "rest on the butterfly", ok, "%s; opening->open %.2fs" % (p.states(), (t_open - t_opening) if ok else -1))
    card_h = status().get("card_height") or 400

    # 2 into the card across the gap
    with Poller() as p:
        vp("glide:%d,%d,220" % (X, bar_h + 34), "sleep:0.5", "glide:%d,%d,300" % (X - 120, min(bar_h + 34 + card_h // 2, 500)), "sleep:0.8")
    ok = all(r[2] is True for r in p.rows) and p.rows[-1][1] == "open"
    report(2, "move down into the card", ok, "%s; open in every one of %d samples: %s" % (p.states(), len(p.rows), all(r[2] is True for r in p.rows)))

    # 3 leave
    with Poller() as p:
        vp(away, "sleep:1.2")
    t_closing, t_closed = p.first("closing"), p.first("closed")
    ok = p.states()[-2:] == ["closing", "closed"] and t_closed - t_closing < 0.8
    report(3, "leave the card", ok, "%s; closing->closed %.2fs" % (p.states(), (t_closed - t_closing) if t_closing is not None and t_closed is not None else -1))

    # 4 fast sweep
    vp("move:%d,%d" % (X - 150, Y), "sleep:0.5")
    with Poller() as p:
        vp("glide:%d,%d,120" % (X + 150, Y), "glide:%d,%d,80" % (X + 200, 300), "sleep:1.0")
    ok = not any(r[2] for r in p.rows)
    report(4, "fast sweep across", ok, "%s; ever open: %s" % (p.states(), any(r[2] for r in p.rows)))

    # 5 another widget: this card must not linger over it
    vp("glide:%d,%d,150" % (X, Y), "sleep:0.9")
    with Poller(also=OTHER_TARGET or None) as p:
        vp("glide:%d,%d,120" % (OTHER_X, Y), "sleep:1.3")
    ok = p.rows[0][2] is True and p.rows[-1][2] is False
    detail = "this card %s" % p.states()
    if OTHER_TARGET:
        other_states = [r[3] for r in p.rows]
        ok = ok and "open" in other_states
        detail += "; the %s card reached open: %s" % (OTHER_TARGET, "open" in other_states)
    report(5, "move to another widget", ok, detail)
    vp(away, "sleep:0.8")

    if "--click" in sys.argv:
        def monarch_windows():
            cl = json.loads(hypr("clients", "-j") or "[]")
            return [c for c in cl if "monarch.com__" in (c.get("class") or "") or "monarch" in (c.get("title") or "").lower()]

        def active():
            w = json.loads(hypr("activewindow", "-j") or "{}")
            return "%s / %s" % (w.get("class") or "", w.get("title") or "")

        before = len(monarch_windows())
        # 6 the card's own control. It sits at the card's bottom right; the pointer walks up that
        # corner until the widget itself says it is on the button, and only then clicks.
        vp("glide:%d,%d,180" % (X, Y), "sleep:0.9")
        st = status()
        h = st.get("card_height") or card_h
        w = st.get("card_width") or 460
        bx = arg("--open-x", X + w // 2 - 60)
        bottom = bar_h + 5 + h
        vp("glide:%d,%d,200" % (X, bar_h + 24), "glide:%d,%d,300" % (bx, bottom - 12), "sleep:0.2")
        by, on_button = bottom - 12, False
        while by > bottom - 160:
            if status().get("open_hovered"):
                on_button = True
                break
            by -= 6
            vp("glide:%d,%d,40" % (bx, by), "sleep:0.12")
        with Poller() as p:
            vp("glide:%d,%d,60" % (bx, by - 3), "sleep:0.3", "click", "sleep:1.5")
        wait_for(lambda: len(monarch_windows()) >= max(1, before))
        wins = monarch_windows()
        ok = on_button and len(wins) >= 1 and p.rows[-1][2] is False and (before >= 1 or len(wins) == before + 1)
        report(6, "click Open Monarch", ok, "pointer on the button: %s; windows before %d, after %d; card open at the end: %s; active: %s" % (
            on_button, before, len(wins), p.rows[-1][2], active()))
        # 7 the butterfly itself: focus something else first, then click
        subprocess.run(["hyprctl", "dispatch", "hl.dsp.focus({ window = \"class:^(foot|com.mitchellh.ghostty|Alacritty|kitty)$\" })"], capture_output=True)
        time.sleep(0.5)
        was = active()
        vp("glide:%d,%d,200" % (X, Y), "sleep:0.15", "click", "sleep:1.5", away)
        wait_for(lambda: "monarch" in active().lower())
        wins2 = monarch_windows()
        ok = len(wins2) == max(1, len(wins)) and "monarch" in active().lower()
        report(7, "click the butterfly", ok, "active before %r, after %r; windows %d (no second one)" % (was, active(), len(wins2)))

    vp("move:%d,%d" % home)
    print("%d/%d gestures passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
