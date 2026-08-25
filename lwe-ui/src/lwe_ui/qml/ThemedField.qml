import QtQuick
import QtQuick.Controls.Basic
import "."

// A themed text field (Basic style has no theming of its own).
TextField {
    id: tf
    color: Theme.textPrimary
    placeholderTextColor: Theme.textTertiary
    font.pixelSize: Theme.fontBody
    selectByMouse: true
    background: Rectangle {
        radius: Theme.radiusMd
        color: Theme.surfaceVariant
        border.width: 1
        border.color: tf.activeFocus ? Theme.accent : Theme.border
    }
}
