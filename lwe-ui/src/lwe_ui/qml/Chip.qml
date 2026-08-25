import QtQuick
import QtQuick.Controls.Basic
import "."

Rectangle {
    id: chip

    property string text: ""
    property string kind: "type"                 // "type" | "forecast" | "fact"
    property color tone: Theme.textTertiary      // kept for callers; chips are text-only
    property color shell: Theme.surfaceVariant

    implicitHeight: 20
    implicitWidth: row.implicitWidth + 14
    radius: Theme.radiusXs
    color: shell

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 5

        Label {
            anchors.verticalCenter: parent.verticalCenter
            text: chip.text
            // imagery law (v2.3.7): on the dark scrim plate (chip over artwork) the label is
            // the fixed badge text on all 14 themes - themed text2 goes dark-on-dark on light
            color: Qt.colorEqual(chip.shell, Theme.scrimPlate) ? Theme.badgeText : Theme.textSecondary
            font.pixelSize: Theme.fontMeta
        }
    }
}
