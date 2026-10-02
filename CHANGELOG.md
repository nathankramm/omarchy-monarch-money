# Changelog

All notable changes to Pupa are recorded here. Versions follow [Semantic Versioning](https://semver.org/).

## v0.1.0 (2026-10-01)

The first public release: a starting stage.

### The widget
- A butterfly on the Omarchy bar, tinted by this month's spending pace against your usual
  month: green under or on pace, amber over, never red. No number is ever shown on the bar.
- A hover card: month to date, the pace line worded against the day ("over a usual day 12"),
  "on pace for ... · usual ...", a chart against the usual month with the middle half of your
  months as a band, up to three chips naming the spending groups furthest from usual, a
  segmented bar and a row per group, the next recurring bill, and the age of the bank data.
- Click opens or focuses the Monarch web app when one is installed, and otherwise opens Monarch
  in the default browser. Middle click fetches now.
- A grace period: on days 1 to 3 of a month the butterfly stays neutral, while the card keeps
  every figure (`pace_grace_days`).
- Fail-empty face states: a greyed butterfly for data over 3 hours old, a broken-link glyph when
  signed out, without data, or with data over 36 hours old. A butterfly is never painted over
  bad data.

### The engine (`monarch-now`)
- Read-only by construction and by guard: every GraphQL document is parsed and anything but a
  query is refused before it is sent.
- Sign-in in a real Chrome or Chromium window with a throwaway profile; the session goes
  straight to the Secret Service keyring through `secret-tool`.
- `monarchmoneycommunity` and all of its dependencies pinned by hash.
- The usual month is computed from your own history: the complete months on record, the newest
  twelve at most.
- An optional `config.toml`: the grace period, the one-off tag, further excluded tags, the
  first month the usual month may use, and your own spending groups (`config.example.toml`).
- `monarch-now status` (no amounts) and `monarch-now check FROM TO` for cross-checking against a
  Monarch report.

### Around it
- `install.sh` (engine, private venv, `--require-hashes`) and the optional `install-webapp.sh`
  (a Monarch web app through `omarchy-webapp-install`; the icon is fetched at install time).
- Tests on fully synthetic data: T9 (the model against an independent oracle, with mutants),
  T10 (the bar tick end to end, with mutants), an open-path test, and a virtual-pointer gesture
  test for the live widget.
- `tools/demo.py`: an invented household for trying the widget, and for screenshots, without a
  Monarch account.
