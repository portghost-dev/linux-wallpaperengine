import QtQuick
import "."

Item {
    id: pin

    property int size: 12
    property color color: Theme.textSecondary

    implicitWidth: size
    implicitHeight: size
    width: size
    height: size

    readonly property real u: size / 11

    Rectangle {
        x: 4.5 * pin.u
        y: 0
        width: 2 * pin.u
        height: 7 * pin.u
        radius: 1 * pin.u
        color: pin.color
    }
    Rectangle {
        x: 2 * pin.u
        y: 6.5 * pin.u
        width: 7 * pin.u
        height: 2 * pin.u
        radius: 1 * pin.u
        color: pin.color
    }
    Rectangle {
        x: 4.9 * pin.u
        y: 8 * pin.u
        width: 1.2 * pin.u
        height: 3 * pin.u
        color: pin.color
    }
}
