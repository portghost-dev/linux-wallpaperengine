import QtQuick
import QtQuick.Shapes
import "."

// The shuffle mark: two paths that cross, each ending in an arrow head, on a 24 unit grid
// drawn at `size` like IconSun. The stroke is 1.8 px at the drawn size.
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
            // lower-left to upper-right, arrow head at the end
            startX: 2; startY: 18
            PathLine { x: 7; y: 18 }
            PathLine { x: 17; y: 6 }
            PathLine { x: 22; y: 6 }
            PathMove { x: 19; y: 3 }
            PathLine { x: 22; y: 6 }
            PathLine { x: 19; y: 9 }
            // upper-left to lower-right, arrow head at the end
            PathMove { x: 2; y: 6 }
            PathLine { x: 7; y: 6 }
            PathLine { x: 17; y: 18 }
            PathLine { x: 22; y: 18 }
            PathMove { x: 19; y: 15 }
            PathLine { x: 22; y: 18 }
            PathLine { x: 19; y: 21 }
        }
    }
}
