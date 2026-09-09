import QtQuick
import QtQuick.Shapes
import "."

// The search mark of the header's icon buttons: a circle of radius 6.5 with a short handle.
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
            PathAngleArc { centerX: 10.5; centerY: 10.5; radiusX: 6.5; radiusY: 6.5; startAngle: 0; sweepAngle: 360 }
        }
        ShapePath {
            fillColor: "transparent"
            strokeColor: mark.color
            strokeWidth: mark.stroke
            capStyle: ShapePath.RoundCap
            startX: 15.3; startY: 15.3
            PathLine { x: 20.5; y: 20.5 }
        }
    }
}
