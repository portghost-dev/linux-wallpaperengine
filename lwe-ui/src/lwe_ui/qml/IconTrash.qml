import QtQuick
import QtQuick.Shapes
import "."

Item {
    id: trash

    property int size: 16
    property color color: Theme.textPrimary

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: trash.width / 24; yScale: trash.height / 24 }
        ShapePath {
            strokeColor: trash.color
            strokeWidth: 1.7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg {
                path: "M4 7 H20 M10 11 V17 M14 11 V17 M5 7 L6 19 A2 2 0 0 0 8 21 H16 A2 2 0 0 0 18 19 L19 7 M9 7 V4 A1 1 0 0 1 10 3 H14 A1 1 0 0 1 15 4 V7"
            }
        }
    }
}
