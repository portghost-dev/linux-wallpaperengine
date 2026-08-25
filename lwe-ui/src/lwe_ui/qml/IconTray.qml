import QtQuick
import "."

Item {
    id: tray

    property int size: 12
    property color color: Theme.textSecondary

    readonly property real u: size / 15

    implicitWidth: size
    implicitHeight: size
    width: implicitWidth
    height: implicitHeight

    readonly property real stroke: Math.max(1, 1.5 * u)

    Item {
        width: tray.size
        height: 11 * tray.u
        anchors.centerIn: parent

        Rectangle {
            anchors.fill: parent
            radius: 3 * tray.u
            color: "transparent"
            border.width: tray.stroke
            border.color: tray.color
        }
        Rectangle {
            x: 2 * tray.u
            width: parent.width - 4 * tray.u
            height: tray.stroke
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 1 * tray.u
            color: tray.color
        }
    }
}
