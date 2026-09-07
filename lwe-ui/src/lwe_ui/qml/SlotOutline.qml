import QtQuick
import QtQuick.Shapes
import "."

// A dotted 1.5 px outline over one grid slot: the origin slot of a lifted card and
// the slot under the pointer. Positioned in the grid's content coordinates from the row index.
Item {
    id: slot

    property int row: -1
    property color color: Theme.hairline
    readonly property real r: 8

    x: (row % grid.cols) * grid.cellWidth
    y: grid.originY + Math.floor(row / grid.cols) * grid.cellHeight
       + (backend.orderModel.hairlineIndex >= 0 && row >= backend.orderModel.hairlineIndex ? grid.poolOffset : 0)
    width: grid.tileW
    height: grid.cellHeight - grid.gap

    Shape {
        anchors.fill: parent
        antialiasing: true
        ShapePath {
            strokeColor: slot.color
            strokeWidth: 1.5
            strokeStyle: ShapePath.DashLine
            dashPattern: [1, 2]
            fillColor: "transparent"
            startX: slot.r; startY: 0.75
            PathLine { x: slot.width - slot.r; y: 0.75 }
            PathArc { x: slot.width - 0.75; y: slot.r; radiusX: slot.r; radiusY: slot.r }
            PathLine { x: slot.width - 0.75; y: slot.height - slot.r }
            PathArc { x: slot.width - slot.r; y: slot.height - 0.75; radiusX: slot.r; radiusY: slot.r }
            PathLine { x: slot.r; y: slot.height - 0.75 }
            PathArc { x: 0.75; y: slot.height - slot.r; radiusX: slot.r; radiusY: slot.r }
            PathLine { x: 0.75; y: slot.r }
            PathArc { x: slot.r; y: 0.75; radiusX: slot.r; radiusY: slot.r }
        }
    }
}
