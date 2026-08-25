import QtQuick
import QtQuick.Shapes
import "."

Item {
    id: mark

    property int size: 26
    property color color: Theme.warning

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: mark.width / 24; yScale: mark.height / 24 }

        // handle tab
        ShapePath {
            strokeColor: mark.color
            strokeWidth: 1.7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            startX: 9.5; startY: 5.4
            PathLine { x: 9.5;  y: 3.4 }
            PathLine { x: 14.5; y: 3.4 }
            PathLine { x: 14.5; y: 5.4 }
        }
        // lid
        ShapePath {
            strokeColor: mark.color
            strokeWidth: 1.7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            startX: 3.5; startY: 6.4
            PathLine { x: 20.5; y: 6.4 }
        }
        // body, tapered
        ShapePath {
            strokeColor: mark.color
            strokeWidth: 1.7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            startX: 5.4; startY: 6.4
            PathLine { x: 6.9;  y: 20.6 }
            PathLine { x: 17.1; y: 20.6 }
            PathLine { x: 18.6; y: 6.4 }
        }
        // arrow head, filled - inside the body
        ShapePath {
            fillColor: mark.color
            strokeColor: "transparent"
            strokeWidth: -1
            startX: 12; startY: 8.8
            PathLine { x: 15.4; y: 12.6 }
            PathLine { x: 8.6;  y: 12.6 }
        }
    }

    // arrow stem - a plain rect so its weight cannot drift from the head
    Rectangle {
        width: Math.max(1.7, mark.size * 0.072)
        height: mark.size * 0.24
        radius: width / 2
        color: mark.color
        x: mark.size * 0.5 - width / 2
        y: mark.size * 0.505
    }
}
