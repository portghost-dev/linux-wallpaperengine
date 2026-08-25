import QtQuick
import QtQuick.Controls.Basic
import "."

Switch {
    id: sw
    property int pillWidth: 30
    property int pillHeight: 17
    implicitWidth: Math.round(pillWidth * 1.25)
    implicitHeight: Math.round(pillHeight * 1.25)

    property color onTrackColor: Theme.accent
    property color onKnobColor: Theme.toggleKnobOn

    indicator: Rectangle {
        implicitWidth: sw.pillWidth
        implicitHeight: sw.pillHeight
        anchors.centerIn: parent
        radius: height / 2
        color: sw.checked ? sw.onTrackColor : Theme.toggleOffTrack
        border.width: 1
        border.color: sw.checked ? sw.onTrackColor : Theme.toggleOffBorder
        Rectangle {
            x: sw.checked ? parent.width - width - 2 : 2
            anchors.verticalCenter: parent.verticalCenter
            width: parent.height - 4
            height: parent.height - 4
            radius: height / 2
            color: sw.checked ? sw.onKnobColor : Theme.toggleOffKnob
            Behavior on x { NumberAnimation { duration: 120 } }
        }
    }
    contentItem: Item {}
}
