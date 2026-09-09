import QtQuick
import QtQuick.Shapes
import "."

// The static mark: a repeat loop, two arrows chasing each other, on a 24 unit grid drawn at
// `size` like IconSun. The stroke is 1.8 px at the drawn size.
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
            // top run, left to right, arrow head at the right end
            startX: 4; startY: 12
            PathLine { x: 4; y: 9 }
            PathQuad { x: 7; y: 6; controlX: 4; controlY: 6 }
            PathLine { x: 20; y: 6 }
            PathMove { x: 17; y: 3 }
            PathLine { x: 20; y: 6 }
            PathLine { x: 17; y: 9 }
            // bottom run, right to left, arrow head at the left end
            PathMove { x: 20; y: 12 }
            PathLine { x: 20; y: 15 }
            PathQuad { x: 17; y: 18; controlX: 20; controlY: 18 }
            PathLine { x: 4; y: 18 }
            PathMove { x: 7; y: 15 }
            PathLine { x: 4; y: 18 }
            PathLine { x: 7; y: 21 }
        }
    }
}
