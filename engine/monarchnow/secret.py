"""The Monarch session lives in the Secret Service keyring (gnome-keyring on Omarchy), through
libsecret's `secret-tool`. Never in a file, never in a log, never on argv: the secret crosses a
pipe on stdin (store) or stdout (lookup) and nowhere else.

The item: label "Monarch session (monarch-now)", attributes service=monarch-now kind=session.
The value is a small JSON object of cookie name -> value.
"""
import json
import subprocess

ATTRS = ["service", "monarch-now", "kind", "session"]
LABEL = "Monarch session (monarch-now)"
TOOL = "/usr/bin/secret-tool"
TIMEOUT = 15


class KeyringError(Exception):
    pass


def store(cookies):
    payload = json.dumps(cookies, separators=(",", ":"))
    try:
        p = subprocess.run([TOOL, "store", "--label=" + LABEL] + ATTRS, input=payload, text=True,
                           capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KeyringError("secret-tool store: %s" % type(e).__name__)
    if p.returncode != 0:
        raise KeyringError("secret-tool store exited %d" % p.returncode)


def load():
    """The cookie dict, or None when nothing is stored. KeyringError when the keyring cannot be asked."""
    try:
        p = subprocess.run([TOOL, "lookup"] + ATTRS, text=True, capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KeyringError("secret-tool lookup: %s" % type(e).__name__)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        doc = json.loads(p.stdout)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) and doc else None


def clear():
    try:
        subprocess.run([TOOL, "clear"] + ATTRS, capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KeyringError("secret-tool clear: %s" % type(e).__name__)
