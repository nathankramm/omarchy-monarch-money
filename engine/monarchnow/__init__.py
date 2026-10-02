"""monarch-now: the engine behind Pupa, the Monarch Money bar plugin for Omarchy (pupa.monarch).

    adapter.py   the ONE module that talks to Monarch (swap it to change the data source)
    model.py     pure: a snapshot + a clock -> every display string the card paints
    fmt.py       every number -> text
    groups.py    Monarch category -> the card's spending groups (defaults; the config can replace them)
    store.py     cache (0600), state, the non-blocking lock, the log (no amounts)
    secret.py    the session in the Secret Service keyring, through secret-tool
    login.py     sign-in in a real browser window; the session goes straight to the keyring
    cli.py       the bar tick, refresh, login/logout, status, check, the config file

Iron rule: the engine formats, the painter paints. Python does all math and all formatting; the
QML multiplies a ratio by a pixel width and nothing else.
"""

__version__ = "0.1.0"
