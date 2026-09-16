import QtQuick
import QtQuick.Controls.Basic
import "."

// Restart verb of a restart-class settings row: visible while the engine process and the
// env file differ on the row's key. The bridge owns the restart and the settle window,
// which outlive the page.
Rectangle {
    id: verb

    property string settingKey: ""
    // the page's rev and restartRev: a commit or a recheck re-reads the bridge
    property int rev: 0
    property int restartRev: 0

    readonly property bool pending: (verb.rev, verb.restartRev,
                                     settingsBridge.restartPending(verb.settingKey))
    readonly property bool busy: settingsBridge.restartBusy
    // the verb that took the restart stays up, dimmed, until the window closes
    property bool held: false
    onBusyChanged: if (!busy) held = false

    visible: pending || held
    enabled: !busy
    opacity: busy ? 0.5 : 1.0
    width: 24
    height: 24
    radius: 5
    color: hover.hovered && !busy ? Theme.dangerWash : "transparent"
    border.width: 1
    border.color: Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.45)

    IconRestart { anchors.centerIn: parent; size: 14; color: Theme.danger }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler {
        enabled: !verb.busy
        onTapped: { if (settingsBridge.takeRestart()) verb.held = true }
    }
    ToolTip.visible: hover.hovered
    ToolTip.text: verb.busy ? "Restarting the engine" : "Restart the engine to apply"
    ToolTip.delay: 300
}
