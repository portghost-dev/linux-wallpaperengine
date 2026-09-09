import QtQuick
import QtQuick.Shapes
import "."

// The close mark of the header's icon buttons: two diagonals corner to corner, inset 6 units.
// Drawn on a 24 unit grid at `size`; the stroke is 1.6 px at the drawn size, round caps and joins.
Item {
    id: mark

    property int size: 14
    property color color: Theme.textSecondary
    readonly property real stroke: 1.6 * 24 / Math.max(1, size)

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: mark.width / 24; yScale: mark.height / 24 }
        ShapePath {
            fillColor: "transparent"
            strokeColor: mark.color
            strokeWidth: mark.stroke
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            startX: 6; startY: 6
            PathLine { x: 18; y: 18 }
            PathMove { x: 18; y: 6 }
            PathLine { x: 6; y: 18 }
        }
    }
}
