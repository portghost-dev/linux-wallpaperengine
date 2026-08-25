import QtQuick
import "."

Item {
    id: code

    property int size: 12
    property color color: Theme.textSecondary

    // scale factor off a 16px reference width (left corner + slash + right corner)
    readonly property real u: size / 16

    implicitWidth: size
    implicitHeight: size
    width: implicitWidth
    height: implicitHeight

    readonly property real stroke: Math.max(1, 1.5 * u)
    readonly property real arm: 5 * u

    Item {
        width: code.size
        height: 11 * code.u
        anchors.centerIn: parent

        Item {
            width: code.arm; height: code.arm
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            rotation: 135
            Rectangle { width: code.stroke; height: parent.height; anchors.right: parent.right; radius: code.stroke / 2; color: code.color }
            Rectangle { width: parent.width; height: code.stroke; anchors.bottom: parent.bottom; radius: code.stroke / 2; color: code.color }
        }
        Rectangle {
            anchors.centerIn: parent
            width: code.stroke
            height: 11 * code.u
            radius: code.stroke / 2
            color: code.color
            rotation: 20
        }
        Item {
            width: code.arm; height: code.arm
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            rotation: -45
            Rectangle { width: code.stroke; height: parent.height; anchors.right: parent.right; radius: code.stroke / 2; color: code.color }
            Rectangle { width: parent.width; height: code.stroke; anchors.bottom: parent.bottom; radius: code.stroke / 2; color: code.color }
        }
    }
}
