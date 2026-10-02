pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons

// Pupa's hover card: hero · chart · one note · up to three chips · one segmented bar · group
// rows in tabular columns · a footer.
//
// Every string here is a whole field from the engine (monarch-now, model.py). This file does no
// arithmetic on money and builds no text. Its only sums are pixels: a normalised chart point
// times the plot's size, a weight ratio times the bar's width.
//
// COLOR. GREEN means under or at a usual month and AMBER means over it; nothing is red, so
// spending never reads as an alarm. The engine sends a direction ("good", "over", "flat",
// "none") and `host.dirColor` turns it into the theme's green or yellow. The group swatches are
// the engine's own hexes; they never carry a status.
Item {
  id: hc

  property var doc: null                 // the engine's `card` object, or null
  property string faceState: "pending"
  property string fallbackText: ""       // shown when there is no card at all
  property var host: null                // BarWidget: dirColor(), goodColor, overColor
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property int cardWidth: Style.space(428)   // + the popup's insets = 460 on screen

  signal openRequested()
  // True while the pointer is on the "Open Monarch" button (status only: the gesture test
  // uses it to know its click will land on the button).
  readonly property bool openHovered: openArea.containsMouse

  readonly property color muted: Qt.darker(foreground, 1.4)
  readonly property color faint: Qt.darker(foreground, 1.9)
  readonly property color warn: host ? host.overColor : "#d9a441"
  readonly property var hero: doc && doc.hero ? doc.hero : null
  readonly property var chart: doc && doc.chart ? doc.chart : null
  readonly property var chips: doc && doc.chips ? doc.chips : []
  readonly property var groups: doc && doc.groups ? doc.groups : []
  readonly property var footer: doc && doc.footer ? doc.footer : null
  readonly property int shownSegments: {
    var n = 0
    for (var i = 0; i < groups.length; i++) if (Number(groups[i].weight_ratio) > 0) n++
    return n
  }

  function tint(dir, fallback) { return host ? host.dirColor(dir, fallback) : fallback }

  width: parent ? parent.width : cardWidth
  implicitWidth: cardWidth
  implicitHeight: column.implicitHeight

  Column {
    id: column
    width: parent.width
    spacing: Style.space(6)

    // ---- hero: month to date · the day pill
    Item {
      visible: hc.hero !== null
      width: parent.width
      height: visible ? heroTotal.implicitHeight : 0

      Text {
        id: heroTotal
        textFormat: Text.PlainText
        text: hc.hero ? hc.hero.total_text : ""
        color: hc.foreground
        font.family: hc.fontFamily
        font.pixelSize: Style.font.display
        font.bold: true
        font.features: ({ "tnum": 1 })
      }

      Text {
        anchors.left: heroTotal.right
        anchors.leftMargin: Style.space(8)
        anchors.baseline: heroTotal.baseline
        textFormat: Text.PlainText
        text: hc.hero ? hc.hero.total_label : ""
        color: hc.muted
        font.family: hc.fontFamily
        font.pixelSize: Style.font.caption
      }

      Rectangle {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: pillText.implicitWidth + Style.space(12)
        height: pillText.implicitHeight + Style.space(4)
        radius: height / 2
        color: Style.normalFillFor(hc.foreground, Color.accent, Color.urgent)
        border.width: 1
        border.color: Style.normalBorderFor(hc.foreground, Color.accent, Color.urgent)

        Text {
          id: pillText
          anchors.centerIn: parent
          textFormat: Text.PlainText
          text: hc.hero ? hc.hero.pill_text : ""
          color: hc.muted
          font.family: hc.fontFamily
          font.pixelSize: Style.font.caption
          font.features: ({ "tnum": 1 })
        }
      }
    }

    // ---- "▼ $X under a usual day N" · "on pace for … · usual …" (beside it when it fits, else below)
    Item {
      id: paceRow
      visible: hc.hero !== null
      width: parent.width
      readonly property bool oneLine: paceText.implicitWidth + paceDetail.implicitWidth + Style.space(12) <= width
      height: !visible ? 0 : (oneLine ? paceText.implicitHeight : paceText.implicitHeight + (paceDetail.text !== "" ? paceDetail.implicitHeight + Style.space(2) : 0))

      Text {
        id: paceText
        anchors.left: parent.left
        textFormat: Text.PlainText
        text: hc.hero ? hc.hero.pace_text : ""
        color: hc.tint(hc.hero ? hc.hero.pace_dir : "none", hc.foreground)
        font.family: hc.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
        font.features: ({ "tnum": 1 })
      }

      Text {
        id: paceDetail
        x: paceRow.oneLine ? parent.width - implicitWidth : 0
        y: paceRow.oneLine ? paceText.baselineOffset - baselineOffset : paceText.implicitHeight + Style.space(2)
        textFormat: Text.PlainText
        text: hc.hero ? hc.hero.detail_text : ""
        color: hc.muted
        font.family: hc.fontFamily
        font.pixelSize: Style.font.caption
        font.features: ({ "tnum": 1 })
      }
    }

    // ---- what is wrong, when something is: stale data, a failed check, a broken feed
    Text {
      visible: text !== ""
      width: parent.width
      textFormat: Text.PlainText
      text: hc.doc ? (hc.doc.banner_text || "") : hc.fallbackText
      color: hc.hero ? hc.warn : hc.foreground
      font.family: hc.fontFamily
      font.pixelSize: hc.hero ? Style.font.caption : Style.font.body
      font.bold: hc.hero === null
      wrapMode: Text.Wrap
    }

    Text {
      visible: text !== ""
      width: parent.width
      textFormat: Text.PlainText
      text: hc.doc ? (hc.doc.hint_text || "") : ""
      color: hc.muted
      font.family: hc.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
    }

    // ---- the chart: this month's running total against the usual month. Normalised points
    //      times pixels, nothing else.
    Item {
      visible: hc.chart !== null
      width: parent.width
      height: visible ? plot.height : 0

      Rectangle {
        id: plot
        width: parent.width
        height: Style.space(96)
        radius: Style.cornerRadius
        color: Util.alpha(hc.foreground, 0.05)

        Canvas {
          id: canvas
          anchors.fill: parent
          readonly property var cur: hc.chart ? hc.chart.current || [] : []
          readonly property var usual: hc.chart ? hc.chart.usual || [] : []
          readonly property var band: hc.chart ? hc.chart.band || [] : []
          readonly property real todayX: hc.chart && typeof hc.chart.today_x === "number" ? hc.chart.today_x : -1
          readonly property real zeroY: hc.chart && typeof hc.chart.zero_y === "number" ? hc.chart.zero_y : 0
          readonly property color lineColor: hc.tint(hc.chart ? hc.chart.dir : "none", Color.accent)
          readonly property color surface: Color.popups.background
          onCurChanged: requestPaint()
          onUsualChanged: requestPaint()
          onBandChanged: requestPaint()
          onLineColorChanged: requestPaint()
          onWidthChanged: requestPaint()
          onHeightChanged: requestPaint()

          function rgba(c, a) { return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + "," + Math.round(c.b * 255) + "," + a + ")" }
          function px(p) { return p.x * width }
          function py(v) { return (1 - v) * height }
          function trace(ctx, pts) {
            for (var i = 0; i < pts.length; i++) {
              if (i === 0) ctx.moveTo(px(pts[i]), py(pts[i].y)); else ctx.lineTo(px(pts[i]), py(pts[i].y))
            }
          }
          onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            // the middle half of the baseline months, as a faint band
            if (band.length > 1) {
              ctx.fillStyle = rgba(hc.foreground, 0.09)
              ctx.beginPath()
              for (var i = 0; i < band.length; i++) {
                if (i === 0) ctx.moveTo(px(band[i]), py(band[i].hi)); else ctx.lineTo(px(band[i]), py(band[i].hi))
              }
              for (var j = band.length - 1; j >= 0; j--) ctx.lineTo(px(band[j]), py(band[j].lo))
              ctx.closePath()
              ctx.fill()
            }
            // zero, only when the scale goes below it (refunds)
            if (zeroY > 0.001) {
              ctx.setLineDash([2, 3]); ctx.strokeStyle = rgba(hc.muted, 0.6); ctx.lineWidth = 1
              ctx.beginPath(); ctx.moveTo(0, py(zeroY)); ctx.lineTo(width, py(zeroY)); ctx.stroke()
            }
            // today, a hairline down the plot
            if (todayX >= 0) {
              ctx.setLineDash([]); ctx.strokeStyle = rgba(hc.foreground, 0.22); ctx.lineWidth = 1
              ctx.beginPath(); ctx.moveTo(Math.round(todayX * width) + 0.5, 0); ctx.lineTo(Math.round(todayX * width) + 0.5, height); ctx.stroke()
            }
            // the usual month, dashed
            if (usual.length > 1) {
              ctx.setLineDash([4, 3]); ctx.strokeStyle = rgba(hc.muted, 0.95); ctx.lineWidth = 1.2
              ctx.beginPath(); trace(ctx, usual); ctx.stroke()
            }
            // this month, tinted by the pace, its last point (today) marked
            if (cur.length > 0) {
              ctx.setLineDash([]); ctx.strokeStyle = rgba(lineColor, 1); ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round"
              if (cur.length > 1) { ctx.beginPath(); trace(ctx, cur); ctx.stroke() }
              var last = cur[cur.length - 1]
              // kept whole inside the plot: on the last day of a month the point sits on the edge
              var dx = Math.max(5, Math.min(width - 5, px(last))), dy = Math.max(5, Math.min(height - 5, py(last.y)))
              ctx.fillStyle = rgba(surface, 1); ctx.beginPath(); ctx.arc(dx, dy, 5, 0, Math.PI * 2); ctx.fill()
              ctx.fillStyle = rgba(lineColor, 1); ctx.beginPath(); ctx.arc(dx, dy, 3, 0, Math.PI * 2); ctx.fill()
            }
          }
        }

        Text { x: Style.space(4); y: Style.space(2); textFormat: Text.PlainText; text: hc.chart ? hc.chart.max_text || "" : ""; color: hc.muted; font.family: hc.fontFamily; font.pixelSize: Style.font.caption; font.features: ({ "tnum": 1 }) }
        Text { x: Style.space(4); anchors.bottom: parent.bottom; anchors.bottomMargin: Style.space(2); textFormat: Text.PlainText; text: hc.chart ? hc.chart.min_text || "" : ""; color: hc.muted; font.family: hc.fontFamily; font.pixelSize: Style.font.caption; font.features: ({ "tnum": 1 }) }
        Text { anchors.right: parent.right; anchors.rightMargin: Style.space(4); anchors.bottom: parent.bottom; anchors.bottomMargin: Style.space(2); textFormat: Text.PlainText; text: hc.chart ? hc.chart.span_text || "" : ""; color: hc.muted; font.family: hc.fontFamily; font.pixelSize: Style.font.caption; font.features: ({ "tnum": 1 }) }
      }
    }

    // ---- the legend: one entry per mark, so no series is told apart by color alone
    Row {
      visible: hc.chart !== null
      height: visible ? implicitHeight : 0
      spacing: Style.space(14)

      Repeater {
        model: hc.chart ? hc.chart.legend || [] : []

        Row {
          id: legendItem
          required property var modelData
          spacing: Style.space(5)

          Item {
            width: Style.space(14)
            height: legendText.implicitHeight

            Rectangle {
              visible: legendItem.modelData.kind === "line"
              anchors.verticalCenter: parent.verticalCenter
              width: parent.width; height: 2; radius: 1
              color: hc.tint(hc.chart ? hc.chart.dir : "none", Color.accent)
            }
            Row {
              visible: legendItem.modelData.kind === "dash"
              anchors.verticalCenter: parent.verticalCenter
              spacing: 3
              Repeater { model: 3; Rectangle { width: (Style.space(14) - 6) / 3; height: 1.5; color: hc.muted } }
            }
            Rectangle {
              visible: legendItem.modelData.kind === "band"
              anchors.verticalCenter: parent.verticalCenter
              width: parent.width; height: Style.space(7); radius: 1
              color: Util.alpha(hc.foreground, 0.16)
            }
          }

          Text {
            id: legendText
            textFormat: Text.PlainText
            text: legendItem.modelData.text || ""
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
            font.features: ({ "tnum": 1 })
          }
        }
      }
    }

    // ---- one note: what is left out, what is counted, what "usual" is
    Text {
      visible: text !== ""
      width: parent.width
      textFormat: Text.PlainText
      text: hc.doc ? (hc.doc.note_text || "") : ""
      color: hc.muted
      font.family: hc.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
      maximumLineCount: 2
    }

    // ---- up to three chips: the groups furthest from usual, named as their rows are
    Flow {
      visible: hc.chips.length > 0
      width: parent.width
      spacing: Style.space(6)

      Item {
        width: chipsLabel.implicitWidth + Style.space(2)
        height: chipsLabel.implicitHeight + Style.space(6)
        Text { id: chipsLabel; anchors.verticalCenter: parent.verticalCenter; textFormat: Text.PlainText; text: hc.doc ? (hc.doc.chips_label || "") : ""; color: hc.muted; font.family: hc.fontFamily; font.pixelSize: Style.font.caption }
      }

      Repeater {
        model: hc.chips

        Rectangle {
          id: chip
          required property var modelData
          width: chipName.implicitWidth + chipValue.implicitWidth + Style.space(19)
          height: chipName.implicitHeight + Style.space(6)
          radius: height / 2
          color: Style.normalFillFor(hc.foreground, Color.accent, Color.urgent)
          border.width: 1
          border.color: Style.normalBorderFor(hc.foreground, Color.accent, Color.urgent)

          Text {
            id: chipName
            x: Style.space(7)
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: chip.modelData.label || ""
            color: hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
          }
          Text {
            id: chipValue
            anchors.left: chipName.right
            anchors.leftMargin: Style.space(5)
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: chip.modelData.value_text || ""
            color: hc.tint(chip.modelData.dir, hc.foreground)
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            font.features: ({ "tnum": 1 })
          }
        }
      }
    }

    // ---- one segmented bar by spending group (the engine's weight_ratio as widths)
    Row {
      id: segments
      visible: hc.shownSegments > 0
      width: parent.width
      height: visible ? 6 : 0
      spacing: 2

      Repeater {
        model: hc.groups

        Rectangle {
          required property var modelData
          visible: Number(modelData.weight_ratio) > 0
          height: 6
          radius: 1
          width: Math.max(1, (segments.width - 2 * (hc.shownSegments - 1)) * (Number(modelData.weight_ratio) || 0))
          color: modelData.swatch
        }
      }
    }

    // ---- group rows: swatch · name · vs usual · this month, tabular columns
    Column {
      visible: hc.groups.length > 0
      width: parent.width
      spacing: 0

      Repeater {
        model: hc.groups

        Item {
          id: groupRow
          required property var modelData
          width: parent.width
          height: groupName.implicitHeight + Style.space(7)

          Rectangle { x: 0; y: Style.space(3); width: 6; height: groupName.implicitHeight - Style.space(1); radius: 1; color: groupRow.modelData.swatch }

          Text {
            id: groupName
            x: Style.space(12)
            y: Style.space(2)
            textFormat: Text.PlainText
            text: groupRow.modelData.name || ""
            color: hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            anchors.right: parent.right
            anchors.rightMargin: Style.space(84)
            anchors.baseline: groupName.baseline
            textFormat: Text.PlainText
            text: groupRow.modelData.vs_text || ""
            color: hc.tint(groupRow.modelData.dir, hc.muted)
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
            font.features: ({ "tnum": 1 })
          }

          Text {
            anchors.right: parent.right
            anchors.baseline: groupName.baseline
            textFormat: Text.PlainText
            text: groupRow.modelData.value_text || ""
            color: hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.body
            font.features: ({ "tnum": 1 })
          }
        }
      }
    }

    Rectangle { width: parent.width; height: 1; color: Util.alpha(hc.foreground, 0.18) }

    // ---- footer: the next bill and the door to Monarch; then what is still due, and the two clocks
    Item {
      width: parent.width
      height: Math.max(footLeft.implicitHeight, openLink.height)

      Text {
        id: footLeft
        anchors.left: parent.left
        width: parent.width - openLink.width - Style.space(10)
        textFormat: Text.PlainText
        text: hc.footer ? (hc.footer.left_text || "") : ""
        color: hc.foreground
        font.family: hc.fontFamily
        font.pixelSize: Style.font.caption
        font.features: ({ "tnum": 1 })
        wrapMode: Text.Wrap
        maximumLineCount: 2
      }

      // The one control on the card. A hover-mode popup takes no focus, but it does take clicks.
      Rectangle {
        id: openLink
        visible: openText.text !== ""
        anchors.right: parent.right
        anchors.top: parent.top
        width: visible ? openText.implicitWidth + Style.space(14) : 0
        height: openText.implicitHeight + Style.space(6)
        radius: height / 2
        color: openArea.containsMouse ? Style.hoverFillFor(hc.foreground, Color.accent, Color.urgent) : Style.normalFillFor(hc.foreground, Color.accent, Color.urgent)
        border.width: 1
        border.color: openArea.containsMouse ? Color.accent : Style.normalBorderFor(hc.foreground, Color.accent, Color.urgent)

        Text {
          id: openText
          anchors.centerIn: parent
          textFormat: Text.PlainText
          text: hc.doc ? (hc.doc.open_text || "") : ""
          color: openArea.containsMouse ? Color.accent : hc.foreground
          font.family: hc.fontFamily
          font.pixelSize: Style.font.caption
        }

        MouseArea {
          id: openArea
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: hc.openRequested()
        }
      }
    }

    // What is still due this month, and the two clocks: one line when both fit, else stacked.
    Item {
      id: clocksRow
      visible: footSub.text !== "" || footRight.text !== ""
      width: parent.width
      readonly property bool oneLine: footSub.implicitWidth + footRight.implicitWidth + Style.space(12) <= width
      height: !visible ? 0 : (oneLine ? Math.max(footSub.implicitHeight, footRight.implicitHeight)
        : footSub.implicitHeight + footRight.implicitHeight + Style.space(2))

      Text {
        id: footSub
        anchors.left: parent.left
        textFormat: Text.PlainText
        text: hc.footer ? (hc.footer.left_sub_text || "") : ""
        color: hc.muted
        font.family: hc.fontFamily
        font.pixelSize: Style.font.caption
        font.features: ({ "tnum": 1 })
      }

      Text {
        id: footRight
        x: parent.width - width
        y: clocksRow.oneLine ? 0 : footSub.implicitHeight + Style.space(2)
        width: Math.min(implicitWidth, parent.width)
        horizontalAlignment: Text.AlignRight
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
        text: hc.footer ? (hc.footer.right_text || "") : ""
        color: hc.muted
        font.family: hc.fontFamily
        font.pixelSize: Style.font.caption
        font.features: ({ "tnum": 1 })
      }
    }

    Text {
      visible: text !== ""
      width: parent.width
      textFormat: Text.PlainText
      text: hc.footer ? (hc.footer.attention_text || "") : ""
      color: hc.warn
      font.family: hc.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
    }
  }
}
