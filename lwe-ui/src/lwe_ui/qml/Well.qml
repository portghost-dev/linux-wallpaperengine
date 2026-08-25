import QtQuick
import QtQuick.Controls.Basic
import "."

// Well (component library 19a.2). A recessed, READ-ONLY surface: depth by luminance, no
// border. Two payloads, same 31px footprint:
//   mode "status" -> a 6px dot (dotColor) + a consequence / status line (text2)
//   mode "value"  -> a copyable machine value (mono, text2) + a drawn copy glyph; tap = copyClicked
//
// The note is NEVER a well - typed input is a bordered Field (ThemedField). Two different jobs,
// do not merge (that borrowed-dialog look was the built tell).
Rectangle {
    id: well

    property string mode: "status"
    property string text: ""
    property color dotColor: Theme.textTertiary
    property bool showDot: mode === "status"
    signal copyClicked()

    implicitHeight: 31
    radius: Theme.radiusSm
    color: Theme.inputWell

    Item {
        visible: well.mode === "status"
        anchors.fill: parent
        anchors.leftMargin: Theme.spacingMd
        anchors.rightMargin: Theme.spacingMd

        Rectangle {
            id: statusDot
            width: 6; height: 6; radius: 3
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            visible: well.showDot
            color: well.dotColor
        }
        Label {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: well.showDot ? statusDot.right : parent.left
            anchors.leftMargin: well.showDot ? Theme.spacingSm : 0
            anchors.right: parent.right
            text: well.text
            color: Theme.textSecondary
            font.pixelSize: Theme.fontMeta
            elide: Text.ElideRight
        }
    }

    Item {
        visible: well.mode === "value"
        anchors.fill: parent
        anchors.leftMargin: Theme.spacingMd
        anchors.rightMargin: Theme.spacingMd

        Label {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.right: copyGlyph.left
            anchors.rightMargin: Theme.spacingSm
            text: well.text
            color: Theme.textSecondary
            font.pixelSize: Theme.fontMicro
            font.family: Theme.monoFamily
            elide: Text.ElideMiddle
        }
        Item {
            id: copyGlyph
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 12; height: 12
            Rectangle {
                x: 3; y: 0; width: 8; height: 8; radius: 1.5
                color: "transparent"
                border.width: 1.2
                border.color: Theme.textTertiary
            }
            Rectangle {
                x: 0; y: 3; width: 8; height: 8; radius: 1.5
                color: Theme.inputWell
                border.width: 1.2
                border.color: Theme.textTertiary
            }
        }
        TapHandler { onTapped: well.copyClicked() }
    }
}
