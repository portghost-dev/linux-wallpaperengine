import QtQuick
import QtQuick.Shapes
import "."

// The filter mark of the header's icon buttons: three centred lines, 16, 10 and 4 units wide.
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
            startX: 4; startY: 7
            PathLine { x: 20; y: 7 }
            PathMove { x: 7; y: 12 }
            PathLine { x: 17; y: 12 }
            PathMove { x: 10; y: 17 }
            PathLine { x: 14; y: 17 }
        }
    }
}
