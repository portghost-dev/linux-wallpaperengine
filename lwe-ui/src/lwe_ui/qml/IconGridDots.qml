import QtQuick
import "."

Item {
    id: grid

    property int size: 12
    property color color: Theme.textSecondary

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    readonly property real u: size / 12
    readonly property real cell: 5 * u
    readonly property real gap: 2 * u
    readonly property real stroke: Math.max(1, 1.5 * u)

    Grid {
        anchors.centerIn: parent
        columns: 2
        rowSpacing: grid.gap
        columnSpacing: grid.gap
        Repeater {
            model: 4
            delegate: Rectangle {
                width: grid.cell
                height: grid.cell
                radius: 1.5 * grid.u
                color: "transparent"
                border.width: grid.stroke
                border.color: grid.color
            }
        }
    }
}
