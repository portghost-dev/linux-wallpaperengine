import QtQuick
import "."

// One header meter (amendment B1): a 4x18 vertical bar beside the two-line text block.
// Word rule intact: the VALUE carries the unit symbol, the LABEL carries the metric name.
Item {
    id: meter

    property real fraction: 0        // 0..1, drives the fill height
    property string value: ""        // top line, e.g. "46%" / "612 MB" / "60"
    property string label: ""        // bottom line, e.g. "CPU" / "RAM"
    property bool breached: false    // true flips the fill to danger (Code owns thresholds)

    readonly property int barW: 4
    readonly property int barH: 18
    readonly property int textGap: 6

    implicitWidth: barW + textGap + textBlock.width
    implicitHeight: Math.max(barH, textBlock.height)

    Rectangle {
        id: track
        width: meter.barW
        height: meter.barH
        radius: 2
        color: Qt.rgba(1, 1, 1, 0.12)
        anchors.verticalCenter: parent.verticalCenter

        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            radius: 2
            height: Math.round(Math.max(0, Math.min(1, meter.fraction)) * meter.barH)
            color: meter.breached ? Theme.danger : Theme.accent
        }
    }

    Column {
        id: textBlock
        anchors.left: track.right
        anchors.leftMargin: meter.textGap
        anchors.verticalCenter: parent.verticalCenter
        spacing: 0
        width: Math.max(valueLine.implicitWidth, labelLine.implicitWidth)

        Text {
            id: valueLine
            text: meter.value
            color: Theme.textPrimary
            font.pixelSize: Theme.fontMicro
        }
        Text {
            id: labelLine
            text: meter.label
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMicro
        }
    }
}
