import QtQuick
import QtQuick.Shapes
import "."

// The day mark beside the shipped moon: a small disc and eight rays on a 24 unit grid, drawn
// at `size` like IconMoon. The stroke is 1.8 px at the drawn size, so the rays survive 14 px.
Item {
    id: mark

    property int size: 24
    property color color: Theme.textTertiary
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
            PathAngleArc { centerX: 12; centerY: 12; radiusX: 3.2; radiusY: 3.2; startAngle: 0; sweepAngle: 360 }
        }
        ShapePath {
            fillColor: "transparent"
            strokeColor: mark.color
            strokeWidth: mark.stroke
            capStyle: ShapePath.RoundCap
            startX: 18; startY: 12
            PathLine { x: 23; y: 12 }
            PathMove { x: 6; y: 12 }
            PathLine { x: 1; y: 12 }
            PathMove { x: 12; y: 6 }
            PathLine { x: 12; y: 1 }
            PathMove { x: 12; y: 18 }
            PathLine { x: 12; y: 23 }
            PathMove { x: 16.24; y: 7.76 }
            PathLine { x: 19.78; y: 4.22 }
            PathMove { x: 7.76; y: 16.24 }
            PathLine { x: 4.22; y: 19.78 }
            PathMove { x: 16.24; y: 16.24 }
            PathLine { x: 19.78; y: 19.78 }
            PathMove { x: 7.76; y: 7.76 }
            PathLine { x: 4.22; y: 4.22 }
        }
    }
}
