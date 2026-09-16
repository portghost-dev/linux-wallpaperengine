import QtQuick
import QtQuick.Shapes
import "."

// The restart mark: an open ring travelling clockwise with an arrow head at its end, on a
// 24 unit grid drawn at `size` like IconRepeat. The stroke is 1.8 px at the drawn size.
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
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            // the ring, from the lower right round to the top
            startX: 16.82; startY: 6.25
            PathArc { x: 9.43; y: 4.95; radiusX: 7.5; radiusY: 7.5; useLargeArc: true }
            // the head, open, on the ring's end
            PathMove { x: 6.01; y: 3.84 }
            PathLine { x: 9.43; y: 4.95 }
            PathLine { x: 7.53; y: 8.01 }
        }
    }
}
