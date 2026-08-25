import QtQuick
import QtQuick.Shapes
import "."

Item {
    id: mark

    property int size: 16
    property color color: Theme.textSecondary

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: mark.width / 24; yScale: mark.height / 24 }

        ShapePath {
            strokeColor: mark.color
            strokeWidth: 1.7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            startX: 3; startY: 18.5
            PathLine { x: 3;    y: 5.5 }
            PathLine { x: 9.5;  y: 5.5 }
            PathLine { x: 11.5; y: 8 }
            PathLine { x: 19;   y: 8 }
            PathLine { x: 19;   y: 11.5 }
        }
        // base, stopping short so the lens is not crossed by it
        ShapePath {
            strokeColor: mark.color
            strokeWidth: 1.7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            startX: 3; startY: 18.5
            PathLine { x: 12; y: 18.5 }
        }
        ShapePath {
            strokeColor: mark.color
            strokeWidth: 1.7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: 16.2; centerY: 15.4
                radiusX: 3.6;  radiusY: 3.6
                startAngle: 0; sweepAngle: 360
            }
        }
        ShapePath {
            strokeColor: mark.color
            strokeWidth: 1.7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            startX: 18.9; startY: 18.1
            PathLine { x: 21.2; y: 20.4 }
        }
    }
}
