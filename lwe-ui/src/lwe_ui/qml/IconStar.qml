import QtQuick
import "."

Item {
    id: star

    property int size: 12
    property color color: Theme.textSecondary
    // filled = the solid favorited star; false = the hollow outline (rail / unfavorited)
    property bool filled: false

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    readonly property real u: size / 16
    readonly property real stroke: Math.max(1, 1.5 * u)

    Canvas {
        id: cv
        anchors.fill: parent
        // repaint when the size, color (live re-theme), or fill state changes
        property color penColor: star.color
        property bool fillOn: star.filled
        onPenColorChanged: requestPaint()
        onFillOnChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            var cx = width / 2;
            var cy = height / 2;
            // outline insets by the stroke; the filled star reaches the outline's tip extent
            // (outer + stroke/2) so toggling favorite does not visibly resize the star
            var outer = star.filled ? (width / 2) - star.stroke / 2 : (width / 2) - star.stroke;
            var inner = outer * 0.42;           // classic 5-point inner/outer ratio
            ctx.beginPath();
            for (var i = 0; i < 10; ++i) {
                var r = (i % 2 === 0) ? outer : inner;
                var a = -Math.PI / 2 + i * Math.PI / 5;
                var px = cx + r * Math.cos(a);
                var py = cy + r * Math.sin(a);
                if (i === 0)
                    ctx.moveTo(px, py);
                else
                    ctx.lineTo(px, py);
            }
            ctx.closePath();
            if (star.filled) {
                ctx.fillStyle = star.color;
                ctx.fill();
            } else {
                ctx.lineWidth = star.stroke;
                ctx.lineJoin = "round";
                ctx.strokeStyle = star.color;
                ctx.stroke();
            }
        }
    }
}
