"""Files this engine owns: the snapshot cache, the fetch state, the lock and the log.

    ~/.cache/monarch-now/            0700
        snapshot.json                0600   the last good fetch, normalised (adapter.py's shape)
        state.json                   0600   last attempt, last error class, counters
        lock                                non-blocking flock: a slow fetch is never stacked on
    ~/.config/monarch-now/config.toml       optional, hand-written (config.example.toml).
                                            Read only; this engine never writes it.
    ~/.local/state/monarch-now/log   0600   one line per event. NO dollar amounts, no merchant or
                                            category names, no cookie material: times, counts,
                                            durations and error classes only.
"""
import fcntl
import json
import os
import time

LOG_MAX_BYTES = 200_000
LOG_KEEP_LINES = 500


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "monarch-now")


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "monarch-now")


def _ensure(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "monarch-now", "config.toml")


def snapshot_path():
    return os.path.join(cache_dir(), "snapshot.json")


def state_path():
    return os.path.join(cache_dir(), "state.json")


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def write_json_private(path, doc):
    """Atomic, and 0600 from the first byte: the temp file is created with that mode."""
    _ensure(os.path.dirname(path))
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_snapshot():
    doc = read_json(snapshot_path())
    if not doc or not isinstance(doc.get("fetched_at"), (int, float)) or not isinstance(doc.get("transactions"), list):
        return None
    return doc


def read_state():
    return read_json(state_path()) or {}


def write_state(state):
    write_json_private(state_path(), state)


class Lock:
    """`with Lock() as held:` — held is False when another run has it (serve the cache this tick)."""

    def __init__(self):
        self.fh = None

    def __enter__(self):
        try:
            _ensure(cache_dir())
            fd = os.open(os.path.join(cache_dir(), "lock"), os.O_WRONLY | os.O_CREAT, 0o600)
            os.fchmod(fd, 0o600)          # also for a lock file an older build created 0644
            self.fh = os.fdopen(fd, "w")
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if self.fh:
                self.fh.close()
                self.fh = None
            return False

    def __exit__(self, *exc):
        if self.fh:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
                self.fh.close()
            except OSError:
                pass
        return False


def log(event, **fields):
    """One line: time, event, key=value. Callers pass counts, seconds and error classes ONLY."""
    try:
        path = os.path.join(_ensure(state_dir()), "log")
        line = "%s %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), event,
                               " ".join("%s=%s" % (k, v) for k, v in fields.items()))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line)
        if os.path.getsize(path) > LOG_MAX_BYTES:
            with open(path, "r", encoding="utf-8") as fh:
                tail = fh.readlines()[-LOG_KEEP_LINES:]
            write_tmp = path + ".tmp"
            fd = os.open(write_tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(tail)
            os.replace(write_tmp, path)
    except OSError:
        pass          # a log that cannot be written must never break the bar
