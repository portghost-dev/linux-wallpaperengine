import QtQuick
import QtQuick.Controls.Basic
import "."

Slider {
    id: sld

    property real tickAt: -1        // 0..1 position of a hash tick; -1 draws none

    // The store-truth position. Consumers bind THIS, never `value`: a QML slider's
    // `value:` binding is broken permanently by the first user drag, after which
    // popup/editor writes to the same key silently stop reflecting here - the exact
    // staleness this surface exists to kill. The Binding below re-asserts store truth
    // whenever the user is not holding the knob, so external writes always land.
    property real storeValue: 0

    signal commit(real v)

    width: 132
    implicitWidth: 132
    implicitHeight: 16

    value: storeValue
    Binding {
        target: sld
        property: "value"
        value: sld.storeValue
        when: !sld.pressed
        restoreMode: Binding.RestoreBindingOrValue
    }

    onPressedChanged: if (!pressed) sld.commit(sld.value)

    background: Rectangle {
        x: sld.leftPadding
        y: sld.topPadding + sld.availableHeight / 2 - height / 2
        width: sld.availableWidth
        height: 3
        radius: 1.5
        color: Theme.border
        Rectangle {
            width: sld.visualPosition * parent.width
            height: parent.height
            radius: 1.5
            color: Theme.accent
        }
        Rectangle {
            visible: sld.tickAt >= 0
            x: sld.tickAt * parent.width - 0.5
            y: -3
            width: 1
            height: 9
            color: Theme.textTertiary
        }
    }
    handle: Rectangle {
        x: sld.leftPadding + sld.visualPosition * (sld.availableWidth - width)
        y: sld.topPadding + sld.availableHeight / 2 - height / 2
        width: 10
        height: 10
        radius: 5
        color: Theme.textPrimary
    }
}
