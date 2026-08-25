import QtQuick
import "."

Item {
    id: chev

    property int size: 12
    property color color: Theme.textSecondary
    // "down" | "left" | "right" | "up"
    property string direction: "down"

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    // The visible corner is ~5/12 of the box, centered, so the glyph reads at any size
    // with the 5x5-in-a-larger-tap proportion. Stroke tracks 1.5px at the 12px box.
    readonly property real stroke: Math.max(1, size * 0.125)
    readonly property real arm: size * 0.42

    Item {
        id: corner
        width: chev.arm
        height: chev.arm
        anchors.centerIn: parent
        rotation: chev.direction === "up" ? -135
                : chev.direction === "right" ? -45
                : chev.direction === "left" ? 135
                : 45
        Rectangle {
            width: chev.stroke
            height: parent.height
            anchors.right: parent.right
            radius: chev.stroke / 2
            color: chev.color
        }
        Rectangle {
            width: parent.width
            height: chev.stroke
            anchors.bottom: parent.bottom
            radius: chev.stroke / 2
            color: chev.color
        }
    }
}
