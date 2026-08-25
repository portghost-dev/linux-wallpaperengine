import QtQuick
import "."

Item {
    id: mag

    property int size: 12
    property color color: Theme.textSecondary

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    readonly property real u: size / 13
    readonly property real stroke: Math.max(1, 1.5 * u)

    Rectangle {
        x: 0
        y: 0
        width: 9 * mag.u
        height: 9 * mag.u
        radius: width / 2
        color: "transparent"
        border.width: mag.stroke
        border.color: mag.color
    }
    Rectangle {
        width: 5 * mag.u
        height: mag.stroke
        radius: mag.stroke / 2
        color: mag.color
        rotation: 45
        x: 9 * mag.u
        y: 9 * mag.u
        transformOrigin: Item.TopLeft
    }
}
