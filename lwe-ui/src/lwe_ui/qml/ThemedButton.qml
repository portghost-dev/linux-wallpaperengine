import QtQuick
import QtQuick.Controls.Basic
import "."

Button {
    id: btn
    property color tint: Theme.textPrimary
    contentItem: Label {
        text: btn.text
        color: btn.tint
        font.pixelSize: Theme.fontLabel
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: Theme.radiusMd
        color: btn.hovered ? Theme.surfaceVariant : "transparent"
        border.width: 1
        border.color: Theme.border
    }
}
