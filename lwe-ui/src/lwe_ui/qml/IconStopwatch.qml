import QtQuick
import QtQuick.Shapes
import "."

// The stopwatch mark for the pill's interval cell: a dial with a crown on top and a side
// button, on a 24 unit grid drawn at `size` like IconSun. The stroke is 1.8 px at the drawn size.
Item {
    id: mark

    property int size: 24
    property color color: Theme.textSecondary
    readonly property real stroke: 1.8 * 24 / Math.max(1, size)

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
            PathAngleArc { centerX: 12; centerY: 14; radiusX: 7.5; radiusY: 7.5; startAngle: 0; sweepAngle: 360 }
        }
        ShapePath {
            fillColor: "transparent"
            strokeColor: mark.color
            strokeWidth: mark.stroke
            capStyle: ShapePath.RoundCap
            // the crown: a stem up from the dial and a cap across it
            startX: 12; startY: 6.5
            PathLine { x: 12; y: 3.5 }
            PathMove { x: 9.5; y: 3 }
            PathLine { x: 14.5; y: 3 }
            // the side button, up and to the right
            PathMove { x: 18; y: 8 }
            PathLine { x: 20; y: 6 }
            // the hand, from the centre toward one o'clock
            PathMove { x: 12; y: 14 }
            PathLine { x: 14.5; y: 10.5 }
        }
    }
}
