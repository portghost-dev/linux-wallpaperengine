import QtQuick
import QtQuick.Controls.Basic
import "."

MenuItem {
    id: mi
    implicitHeight: 26
    contentItem: Label {
        text: mi.text
        color: mi.enabled ? Theme.textPrimary : Theme.textTertiary
        font.pixelSize: Theme.fontControl
        verticalAlignment: Text.AlignVCenter
        leftPadding: Theme.spacingSm
        elide: Text.ElideRight
    }
    background: Rectangle {
        color: mi.highlighted ? Theme.hoverWash : "transparent"
        radius: Theme.radiusXs
    }
}
