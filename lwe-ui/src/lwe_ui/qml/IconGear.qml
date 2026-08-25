import QtQuick
import QtQuick.Shapes
import "."

Item {
    id: gear

    property int size: 16
    property color color: Theme.textSecondary

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    // 24u authoring canvas, scaled to the requested size (stroke + geometry scale together)
    Item {
        width: 24
        height: 24
        scale: gear.size / 24
        transformOrigin: Item.TopLeft

        Repeater {
            model: 8
            delegate: Item {
                required property int index
                width: 24; height: 24
                rotation: index * 45
                transformOrigin: Item.Center
                Rectangle {
                    x: (24 - 3.2) / 2
                    y: 3.1
                    width: 3.2; height: 3.3
                    radius: 0.9
                    color: gear.color
                }
            }
        }

        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: gear.color
                strokeWidth: 1.7
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                startX: 18; startY: 12                       // ring: (12+6, 12)
                PathAngleArc { centerX: 12; centerY: 12; radiusX: 6; radiusY: 6
                               startAngle: 0; sweepAngle: 360 }
            }
            ShapePath {
                strokeColor: gear.color
                strokeWidth: 1.7
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                startX: 14.15; startY: 12                    // hub: (12+2.15, 12)
                PathAngleArc { centerX: 12; centerY: 12; radiusX: 2.15; radiusY: 2.15
                               startAngle: 0; sweepAngle: 360 }
            }
        }
    }
}
