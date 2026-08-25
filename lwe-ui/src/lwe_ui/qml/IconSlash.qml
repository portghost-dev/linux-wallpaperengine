import QtQuick
import "."

Item {
    id: slash

    property int size: 12
    property color color: Theme.textSecondary

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    readonly property real stroke: Math.max(1, size * 0.125)

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: "transparent"
        border.width: slash.stroke
        border.color: slash.color
    }
    // diagonal bar across the ring (45 degrees); length is the diameter so it meets the ring
    Rectangle {
        anchors.centerIn: parent
        width: slash.stroke
        height: slash.size
        radius: slash.stroke / 2
        color: slash.color
        rotation: 45
    }
}
