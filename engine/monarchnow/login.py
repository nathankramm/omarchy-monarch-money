"""`monarch-now login`: you sign in yourself, in a real browser window, and the session goes
straight from the browser to the keyring.

WHY IT WORKS THIS WAY
  - Monarch's web app uses cookie sessions (`session_id`, HttpOnly, plus `csrftoken`). The old
    "copy the token out of localStorage" path is dead: the token is null.
  - Many accounts sign in through Apple or Google, so there is no password for a client to
    post, and the password endpoint is behind a CAPTCHA and an email code anyway.
  - Copying a Cookie header out of DevTools would put the session on the clipboard, and Omarchy
    keeps a plaintext clipboard history on disk. So nothing here is ever copied or typed.

WHAT IT DOES
  1. Starts Chrome (or Chromium) on Monarch's sign-in page with a THROWAWAY profile directory
     (0700, under XDG_RUNTIME_DIR, which is tmpfs) and a DevTools pipe to this process only
     (--remote-debugging-pipe: two inherited file descriptors, no listening port).
  2. Waits while you sign in (password, Google or Apple; the two-factor prompt; "Stay signed in").
  3. Reads the api.monarch.com cookies over the pipe, proves them with ONE read-only query,
     stores them in the keyring, closes the browser and deletes the throwaway profile.
  The session it stores is its own: it is not the one the everyday Monarch web app uses, so
  signing out there does not end this one, and the other way round.

Nothing is printed, logged or written to a file except the keyring item.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import adapter, secret, store

# The first of these on PATH is used; MONARCH_NOW_BROWSER names another Chromium-family binary.
# It must be Chromium-based: the session is read over Chrome's DevTools pipe.
BROWSERS = ("google-chrome-stable", "chromium", "chromium-browser", "brave", "brave-browser")
URL = "https://app.monarch.com/login"
WAIT_SECONDS = 600
POLL_SECONDS = 1.5
REQUIRED = ("session_id", "csrftoken")


class Pipe:
    """Chrome's DevTools protocol over --remote-debugging-pipe: JSON messages ended by NUL."""

    def __init__(self, write_fd, read_fd):
        self.w, self.r = write_fd, read_fd
        self.buf = b""
        self.next = 0
        os.set_blocking(self.r, False)

    def call(self, method, params=None, timeout=10):
        self.next += 1
        want = self.next
        os.write(self.w, json.dumps({"id": want, "method": method, "params": params or {}}).encode() + b"\0")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = os.read(self.r, 1 << 16)
                if chunk == b"":
                    raise RuntimeError("Chrome closed")
                self.buf += chunk
            except BlockingIOError:
                time.sleep(0.05)
            while b"\0" in self.buf:
                raw, self.buf = self.buf.split(b"\0", 1)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("id") == want:
                    if "error" in msg:
                        raise RuntimeError("DevTools refused %s" % method)
                    return msg.get("result") or {}
        raise RuntimeError("no answer to %s" % method)


def _monarch_cookies(pipe):
    """name -> value for the cookies Chrome would send to api.monarch.com."""
    out = {}
    for c in pipe.call("Storage.getCookies").get("cookies", []):
        domain = str(c.get("domain") or "").lstrip(".")
        if domain == "api.monarch.com" or domain == "monarch.com":
            out[c["name"]] = c["value"]
    return out


def find_browser():
    """The Chromium-family binary to sign in with, or None."""
    named = os.environ.get("MONARCH_NOW_BROWSER")
    for name in ([named] if named else BROWSERS):
        path = shutil.which(name)
        if path:
            return path
    return None


def browser_login(out=sys.stdout):
    chrome = find_browser()
    if chrome is None:
        out.write("monarch-now login: no Chrome or Chromium found (looked for %s).\n"
                  "Install one, or set MONARCH_NOW_BROWSER to a Chromium-family browser.\n" % ", ".join(BROWSERS))
        return 1
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    profile = tempfile.mkdtemp(prefix="monarch-now-login-", dir=runtime)
    os.chmod(profile, 0o700)
    to_chrome_r, to_chrome_w = os.pipe()
    from_chrome_r, from_chrome_w = os.pipe()

    def wire():
        # Chrome reads DevTools commands on fd 3 and answers on fd 4.
        a = fcntl.fcntl(to_chrome_r, fcntl.F_DUPFD, 10)
        b = fcntl.fcntl(from_chrome_w, fcntl.F_DUPFD, 10)
        os.dup2(a, 3)
        os.dup2(b, 4)

    proc = None
    pipe = None
    try:
        proc = subprocess.Popen(
            [chrome, "--user-data-dir=" + profile, "--remote-debugging-pipe", "--no-first-run",
             "--no-default-browser-check", "--password-store=basic", "--disable-sync",
             "--class=monarch-now-login", "--new-window", URL],
            preexec_fn=wire, pass_fds=(3, 4), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(to_chrome_r)
        os.close(from_chrome_w)
        pipe = Pipe(to_chrome_w, from_chrome_r)
        out.write(
            "A browser window is opening on Monarch's sign-in page (a throwaway profile).\n"
            "  1. Tick \"Stay signed in\", then sign in the way you usually do (password, Google or\n"
            "     Apple), two-factor code included.\n"
            "  2. Leave the window alone once your Monarch dashboard shows: it closes by itself.\n"
            "Waiting up to %d minutes. Ctrl-C cancels and stores nothing.\n" % (WAIT_SECONDS // 60))
        out.flush()
        deadline = time.monotonic() + WAIT_SECONDS
        tried = None
        last_try = 0.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out.write("The browser was closed before a session appeared. Nothing was stored.\n")
                return 1
            time.sleep(POLL_SECONDS)
            try:
                cookies = _monarch_cookies(pipe)
            except RuntimeError:
                continue
            if not all(k in cookies for k in REQUIRED):
                continue
            pair = tuple(cookies[k] for k in REQUIRED)
            if pair == tried and time.monotonic() - last_try < 10:
                continue                      # same cookies, already refused: do not hammer Monarch
            tried, last_try = pair, time.monotonic()
            session = {k: cookies[k] for k in REQUIRED}
            try:
                adapter.verify(session)
            except adapter.FetchError as e:
                if e.kind == "auth":
                    continue                  # not signed in yet (a pre-login csrf cookie), keep waiting
                out.write("Signed in, but the check query failed (%s). Nothing was stored.\n" % e.kind)
                store.log("login", result="verify-failed", kind=e.kind)
                return 1
            secret.store(session)
            state = store.read_state()
            state.pop("last_error", None)
            state.pop("last_error_detail", None)
            state.pop("last_attempt", None)       # so the next bar tick fetches at once
            state["signed_in_at"] = time.time()
            store.write_state(state)
            store.log("login", result="ok", cookies=len(session))
            out.write("Signed in. The session is in the keyring (\"%s\"), proven with one read-only query.\n" % secret.LABEL)
            poke_bar()
            return 0
        out.write("Timed out waiting for the sign-in. Nothing was stored.\n")
        store.log("login", result="timeout")
        return 1
    except KeyboardInterrupt:
        out.write("\nCanceled. Nothing was stored.\n")
        return 130
    except secret.KeyringError as e:
        out.write("The keyring refused the session (%s). Nothing was stored.\n" % e)
        return 1
    finally:
        if proc is not None:
            try:
                if pipe is not None:
                    pipe.call("Browser.close", timeout=3)
            except Exception:                                   # noqa: BLE001 - closing is best effort
                pass
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def poke_bar():
    """Ask the bar widget to run the engine now instead of at its next tick. Best effort."""
    try:
        subprocess.run(["omarchy-shell", "pupa.monarch", "refresh"], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def paste_login(out=sys.stdout):
    """The fallback: a Cookie header typed or pasted at a hidden prompt. It works, but a paste
    comes off the clipboard, and Omarchy keeps a plaintext clipboard history, so it says so."""
    import getpass
    out.write(
        "Fallback sign-in. In the Monarch web app: DevTools > Network > any `graphql` request >\n"
        "Request Headers > Cookie. NOTE: copying it puts the session in Omarchy's clipboard history\n"
        "(~/.local/state/omarchy/clipboard-history.json, plaintext); clear that entry afterwards.\n")
    raw = getpass.getpass("Cookie header (hidden): ")
    cookies = {}
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k and v:
            cookies[k] = v
    if not all(k in cookies for k in REQUIRED):
        out.write("That did not contain both session_id and csrftoken. Nothing was stored.\n")
        return 1
    session = {k: cookies[k] for k in REQUIRED}
    try:
        adapter.verify(session)
    except adapter.FetchError as e:
        out.write("Monarch did not accept it (%s). Nothing was stored.\n" % e.kind)
        return 1
    secret.store(session)
    state = store.read_state()
    state.pop("last_error", None)
    state.pop("last_error_detail", None)
    state.pop("last_attempt", None)
    state["signed_in_at"] = time.time()
    store.write_state(state)
    store.log("login", result="ok-paste", cookies=len(session))
    out.write("Signed in. The session is in the keyring.\n")
    poke_bar()
    return 0
