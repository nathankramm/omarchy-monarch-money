pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Pupa: Monarch Money on the bar. One butterfly, never a number.
//
//   green   this month's spending is at or under a usual month's pace
//   amber   it is ahead
//   plain   days 1-3 of a month (the grace period), or no complete month to compare with yet
//   grey    the data is more than about three hours old
//   a broken-link glyph   the feed is broken (signed out, no data, engine silent)
//
// Hover opens the card (HoverCard.qml); a click opens or focuses the Monarch web app; a middle
// click asks the engine to fetch now.
//
// THE PAINTER RULE: the engine formats, the painter paints. This file and HoverCard.qml compute
// nothing and build no text. The engine, `monarch-now`, prints one JSON line with the glyph, the
// face state and every string on the card; this file picks a color from the state and hands the
// card its fields.
//
// 🔴 NEVER A PLAUSIBLE FACE OVER BAD DATA. The butterfly is painted only when the last run ended
// in a JSON object whose `face.state` is one this file knows and whose `text` is not blank.
// No output, unparsable output, an unknown state, or the engine's own "broken" all paint the
// broken-link glyph, which no working state can be mistaken for. Every run replaces the face,
// and a run that outlives a whole tick is withdrawn rather than waited on.
BarWidget {
  id: root
  moduleName: "io.github.nathankramm.pupa"

  // nf-md-link_off. Built from the code point: a literal private-use glyph is stripped by some
  // editors and tools, and an empty glyph is an invisible widget.
  readonly property string brokenGlyph: String.fromCodePoint(0xF0338)
  readonly property string unavailableText: "Monarch feed unavailable: monarch-now printed nothing it could read."

  // ---- settings (shell.json entry; manifest.json declares them)
  readonly property string faceExec: {
    var v = root.setting("faceExec", "~/.local/bin/monarch-now")
    return typeof v === "string" ? v.trim() : ""
  }
  readonly property int faceInterval: {
    var v = Number(root.setting("faceInterval", 300))
    return v >= 30 && v <= 86400 ? Math.floor(v) : 300
  }
  readonly property string faceHover: root.setting("faceHover", "card") === "text" ? "text" : "card"
  readonly property bool faceTint: root.setting("faceTint", true) === true
  readonly property bool hoverCardMode: faceHover === "card"

  // ---- the engine's last answer
  property string faceState: "pending"   // pending | good | over | plain | stale | broken
  property string faceText: ""
  property string faceTooltip: ""
  property var faceCard: null
  property string faceReason: ""
  property int runs: 0
  property double lastRunMs: 0
  // A session-only override of the command, for screenshots and tests (IPC `fixture`).
  property string fixtureExec: ""
  readonly property string activeExec: fixtureExec !== "" ? fixtureExec : faceExec

  readonly property var liveStates: ["good", "over", "plain", "stale"]
  readonly property bool faceLive: liveStates.indexOf(faceState) !== -1 && faceText.trim() !== ""
  readonly property string faceShown: faceLive ? faceText : brokenGlyph

  function setBroken(reason) {
    root.faceState = "broken"
    root.faceText = ""
    root.faceTooltip = reason
    root.faceCard = null
    root.faceReason = reason
  }

  function applyFace(raw) {
    root.runs += 1
    root.lastRunMs = Date.now()
    var doc = null
    try {
      var lines = String(raw || "").trim().split("\n")
      doc = JSON.parse(lines[lines.length - 1])
    } catch (e) { doc = null }
    if (!doc || typeof doc !== "object" || !doc.face || typeof doc.face.state !== "string") {
      root.setBroken(root.unavailableText)
      return
    }
    var state = doc.face.state
    var card = doc.card && typeof doc.card === "object" ? doc.card : null
    if (root.liveStates.indexOf(state) !== -1 && typeof doc.text === "string" && doc.text.trim() !== "" && card && card.hero) {
      root.faceState = state
      root.faceText = doc.text
    } else {
      // The engine's own "broken", or anything this file does not know: the glyph is ours.
      root.faceState = "broken"
      root.faceText = ""
    }
    root.faceTooltip = typeof doc.tooltip === "string" && doc.tooltip !== "" ? doc.tooltip : root.unavailableText
    root.faceCard = card
    root.faceReason = typeof doc.face.reason === "string" ? doc.face.reason : ""
  }

  // The command is ASSIGNED for each run, not bound, and the run starts a moment later. With a
  // bound command, a run started from the change handler of a property the binding reads went
  // out with the PREVIOUS command (seen with the IPC `fixture` aid: the real engine ran once
  // more before the fixture took). Assigning removes the ordering question.
  Process {
    id: faceProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyFace(text)
    }
  }

  function runEngine(extra) {
    if (root.activeExec === "") { root.setBroken(root.unavailableText); return }
    if (faceProc.running) return
    faceProc.command = ["bash", "-lc", root.activeExec + (extra || "")]
    startTimer.restart()
  }
  Timer { id: startTimer; interval: 20; onTriggered: if (!faceProc.running) faceProc.running = true }

  // Fetch now (the engine keeps a 60 s floor of its own).
  function refresh() { root.runEngine(root.fixtureExec !== "" ? "" : " --refresh") }

  Timer {
    id: faceTimer
    interval: root.faceInterval * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: {
      // A run that outlives a whole tick is withdrawn, not waited on: a hung engine must not
      // hold the last face on the bar. It is not killed; its answer is painted when it lands.
      if (faceProc.running) root.setBroken("monarch-now has not answered for a whole tick.")
      else root.runEngine("")
    }
  }
  onActiveExecChanged: Qt.callLater(function() { root.runEngine("") })

  // ---- colors: the theme's green, and the theme's yellow for amber. Never red.
  property string themeGreen: ""
  property string themeYellow: ""
  readonly property color baseFg: root.bar ? root.bar.barForeground : Color.foreground
  readonly property color goodColor: themeGreen !== "" ? themeGreen : Color.accent
  readonly property color overColor: themeYellow !== "" ? themeYellow : "#d9a441"

  function parseThemeColors(raw) {
    var lines = String(raw || "").split("\n")
    var found = {}
    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(/^\s*([A-Za-z0-9_-]+)\s*=\s*["']?(#[0-9A-Fa-f]{6})/)
      if (m) found[m[1]] = m[2]
    }
    root.themeGreen = found.green || found.color2 || ""
    root.themeYellow = found.yellow || found.color3 || ""
  }

  FileView {
    path: Color.currentThemePath + "/colors.toml"
    watchChanges: true
    printErrors: false
    onLoaded: root.parseThemeColors(text())
    onLoadFailed: root.parseThemeColors("")
    onFileChanged: reload()
  }

  // good/over/flat/none -> a color. Used by the face and handed to the card.
  function dirColor(dir, fallback) {
    if (dir === "good") return root.goodColor
    if (dir === "over") return root.overColor
    return fallback
  }

  readonly property color faceColor: {
    if (!root.faceTint) return root.baseFg
    if (root.faceState === "good") return root.goodColor
    if (root.faceState === "over") return root.overColor
    return root.baseFg
  }

  // ---- click: the Monarch web app when it is installed (opened or focused by Omarchy's own
  //      helper), else Monarch in the default browser. bin/monarch-open decides.
  readonly property string openCommand: {
    var path = String(Qt.resolvedUrl("bin/monarch-open")).replace(/^file:\/\//, "")
    return "'" + path.replace(/'/g, "'\\''") + "'"
  }
  function openMonarch() {
    root.closeHoverCard()
    if (root.bar) root.bar.run(root.openCommand)
  }

  // ---- the hover card. A passive overlay with no focus grab, so this widget owns its life:
  //        open  after 250 ms of the button hovered;
  //        stay  while the button or the card is hovered;
  //        close 300 ms after both have left, and at once when another owner takes the popout.
  readonly property bool hoverWanted: hoverCardMode && (button.tooltipHovered === true || hoverPopup.containsMouse)
  property string hoverState: "closed"       // closed | opening | open | closing | forced (status only)

  onHoverWantedChanged: {
    if (hoverWanted) {
      closeTimer.stop()
      if (!hoverPopup.open) { root.hoverState = "opening"; openTimer.restart() }
      else root.hoverState = "open"
    } else {
      openTimer.stop()
      if (hoverPopup.open) { root.hoverState = "closing"; closeTimer.restart() }
      else root.hoverState = "closed"
    }
  }
  function closeHoverCard() { openTimer.stop(); closeTimer.stop(); hoverPopup.open = false; root.hoverState = "closed" }

  Timer { id: openTimer; interval: 250; onTriggered: if (root.hoverWanted) { hoverPopup.open = true; root.hoverState = "open" } }
  Timer { id: closeTimer; interval: 300; onTriggered: if (!root.hoverWanted) root.closeHoverCard() }
  Connections {
    target: root.bar
    ignoreUnknownSignals: true
    function onActivePopoutChanged() {
      if (hoverPopup.open && root.bar && root.bar.activePopout !== hoverPopup.coordinatorKey) root.closeHoverCard()
    }
  }

  // The card keeps the PopupCard's own coordinator key (no `owner`).
  PopupCard {
    id: hoverPopup
    anchorItem: button
    bar: root.bar
    triggerMode: "hover"
    // contentWidth/contentHeight are the WHOLE popup, padding and border included, so the insets
    // are added here. The card is 460 wide on screen.
    contentWidth: hoverPopup.fittedContentWidth(hoverCard.implicitWidth + hoverPopup.padding * 2
      + Border.left(hoverPopup.borderSpec) + Border.right(hoverPopup.borderSpec))
    contentHeight: hoverPopup.fittedContentHeight(hoverCard.implicitHeight)
    onOpenChanged: if (!open && root.hoverState !== "closed") { openTimer.stop(); closeTimer.stop(); root.hoverState = "closed" }

    HoverCard {
      id: hoverCard
      doc: root.faceCard
      faceState: root.faceState
      fallbackText: root.faceCard ? "" : root.faceTooltip
      host: root
      foreground: Color.popups.text
      fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
      onOpenRequested: root.openMonarch()
    }
  }

  //   omarchy-shell io.github.nathankramm.pupa status | jq
  //   omarchy-shell io.github.nathankramm.pupa refresh
  //   omarchy-shell io.github.nathankramm.pupa open                      # what a click does
  //   omarchy-shell io.github.nathankramm.pupa hovercard open|close      # screenshot aid
  //   omarchy-shell io.github.nathankramm.pupa fixture '<command>'       # session only; `off` restores
  IpcHandler {
    target: "io.github.nathankramm.pupa"
    function refresh(): void { root.broadcast("refresh") }
    function open(): void { root.openMonarch() }
    function status(): string { return root.statusJson() }
    function hovercard(on: string): void {
      if (on === "open") { hoverPopup.open = true; root.hoverState = "forced" }
      else root.closeHoverCard()
    }
    function fixture(command: string): void { root.fixtureExec = (command === "off" || command === "") ? "" : command }
  }

  // No amounts: states, flags, counts and where the face sits in the bar (for the gesture test).
  function statusJson() {
    var c = root.faceCard
    var at = button.mapToItem(null, button.width / 2, button.height / 2)
    return JSON.stringify({
      face_x: Math.round(at.x),
      face_y: Math.round(at.y),
      face_state: root.faceState,
      face_live: root.faceLive,
      face_glyph: root.faceShown.codePointAt(0).toString(16),
      face_reason: root.faceReason,
      face_tint: root.faceTint,
      fixture: root.fixtureExec !== "",
      runs: root.runs,
      last_run_ms: root.lastRunMs,
      running: faceProc.running,
      interval_s: root.faceInterval,
      hover_mode: root.faceHover,
      hover_state: root.hoverState,
      hover_open: hoverPopup.open,
      open_hovered: hoverCard.openHovered,
      card: c ? {
        hero: !!c.hero,
        pace_dir: c.hero ? c.hero.pace_dir : "",
        chart: !!c.chart,
        chips: c.chips ? c.chips.length : 0,
        groups: c.groups ? c.groups.length : 0,
        banner: c.banner_text !== "",
        unmapped: c.unmapped ? c.unmapped.length : 0
      } : null,
      card_width: hoverPopup.contentWidth,
      card_height: hoverPopup.contentHeight,
      open_command: root.openCommand
    })
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.faceState === "pending" ? "" : root.faceShown
    keepSpace: true
    hasVisualContent: text !== ""
    foreground: root.faceColor
    // Grey = stale. The glyph stays a butterfly; the card says how old the data is.
    dimmed: root.faceState === "stale"
    // With the card the plain tooltip steps aside; "text" shows the engine's fallback text.
    tooltipText: root.hoverCardMode ? "" : root.faceTooltip

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.refresh()
      else if (b === Qt.LeftButton) root.openMonarch()
    }
  }
}
