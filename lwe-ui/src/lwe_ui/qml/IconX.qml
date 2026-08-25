import QtQuick
import "."

Item {
    id: x

    property int size: 12
    property color color: Theme.textSecondary

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    readonly property real stroke: Math.max(1, size * 0.125)
    // reach corner to corner: the 13px bar sits in a ~13-14px box, so ~0.92 of edge
    readonly property real barLen: size * 0.92

    Rectangle {
        anchors.centerIn: parent
        width: x.barLen
        height: x.stroke
        radius: x.stroke / 2
        color: x.color
        rotation: 45
    }
    Rectangle {
        anchors.centerIn: parent
        width: x.barLen
        height: x.stroke
        radius: x.stroke / 2
        color: x.color
        rotation: -45
    }
}
