import QtQuick
import QtQuick.Controls.Basic
import "."

Rectangle {
    id: verb

    property string text: ""
    property bool danger: false
    property bool enabled: true

    signal clicked()

    height: 24
    width: verbLabel.implicitWidth + 18
    radius: 5
    opacity: enabled ? 1.0 : 0.5

    color: hover.hovered && verb.enabled
           ? (danger ? Theme.dangerWash : Theme.surfaceVariant)
           : "transparent"
    border.width: 1
    border.color: danger
                  ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.45)
                  : Theme.borderStrong

    Label {
        id: verbLabel
        anchors.centerIn: parent
        text: verb.text
        color: verb.danger ? Theme.danger : Theme.textPrimary
        font.pixelSize: Theme.fontMeta
    }

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor; enabled: verb.enabled }
    TapHandler { enabled: verb.enabled; onTapped: verb.clicked() }
}
