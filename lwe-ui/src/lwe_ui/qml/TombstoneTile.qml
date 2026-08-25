import QtQuick
import QtQuick.Controls.Basic
import "."

Item {
    id: tile

    property int count: 0
    signal clicked()

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusLg
        color: Qt.rgba(Theme.warning.r, Theme.warning.g, Theme.warning.b,
                       hover.hovered ? 0.12 : 0.07)
        border.width: 1.5
        border.color: Qt.rgba(Theme.warning.r, Theme.warning.g, Theme.warning.b,
                              hover.hovered ? 0.85 : 0.55)
        Behavior on color { ColorAnimation { duration: Motion.wake } }
        Behavior on border.color { ColorAnimation { duration: Motion.wake } }

        Column {
            anchors.centerIn: parent
            spacing: Theme.spacingSm

            IconRestore {
                anchors.horizontalCenter: parent.horizontalCenter
                size: 26
                color: hover.hovered ? Qt.lighter(Theme.warning, 1.15) : Theme.warning
                Behavior on color { ColorAnimation { duration: Motion.wake } }
            }
            Label {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Import " + tile.count + (tile.count === 1 ? " tombstone" : " tombstones")
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBody13
                font.weight: Theme.weightMedium
            }
            Label {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Re-add trashed items"
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMeta
            }
        }
    }

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: tile.clicked() }
}
