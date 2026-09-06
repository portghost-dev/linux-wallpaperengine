import QtQuick
import QtQuick.Shapes
import "."

// A clock face: the same 24-unit box, 1.8 stroke and colour hook as the moon it sits beside.
Item {
    id: mark

    property int size: 24
    property color color: Theme.textTertiary

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
            strokeWidth: 1.8
            capStyle: ShapePath.RoundCap
            PathAngleArc { centerX: 12; centerY: 12; radiusX: 9; radiusY: 9; startAngle: 0; sweepAngle: 360 }
        }
        ShapePath {
            fillColor: "transparent"
            strokeColor: mark.color
            strokeWidth: 1.8
            capStyle: ShapePath.RoundCap
            startX: 12; startY: 7
            PathLine { x: 12; y: 12.5 }
            PathLine { x: 15.5; y: 14.5 }
        }
    }
}
