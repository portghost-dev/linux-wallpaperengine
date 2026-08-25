import QtQuick
import QtQuick.Shapes
import "."

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
            fillColor: mark.color
            strokeColor: "transparent"
            strokeWidth: -1

            PathAngleArc {                    // the disc: its major arc, away from the bite
                centerX: 12.31; centerY: 11.94
                radiusX: 9;     radiusY: 9
                startAngle: 19.140; sweepAngle: 237.351
            }
            PathAngleArc {                    // the bite: near arc, backwards, closing the shape
                centerX: 18.71; centerY: 6.14
                radiusX: 9;     radiusY: 9
                startAngle: -160.860; sweepAngle: -122.649
                moveToStart: false            // must connect, not start a new subpath
            }
        }
    }
}
