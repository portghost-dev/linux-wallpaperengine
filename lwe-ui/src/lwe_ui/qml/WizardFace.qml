import QtQuick
import QtQuick.Controls.Basic
import "."

// The one-skeleton dialog face (16d law, shared with 16e): title + body from the top;
// well / primary / ghost / hairline / fine print pixel-anchored from the bottom, so
// every wizard beat and sibling modal aligns by construction and in-place swaps never
// reflow. Fill the 31px well through `wellContent`.
Item {
    id: face
    property string title: ""
    property string body: ""
    property string primaryText: ""
    property string ghostText: ""
    property string finePrint: "Tombstones live in Settings > Library"
    property color primaryColor: Theme.accent
    property bool wellVisible: true
    property alias wellContent: wellSlot.data
    signal primaryClicked()
    signal ghostClicked()

    anchors.fill: parent

    Label {
        id: faceTitle
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Theme.spacingLg
        text: face.title
        color: Theme.textPrimary
        font.pixelSize: Theme.fontNav
        font.weight: Theme.weightMedium
        elide: Text.ElideRight
    }
    Label {
        anchors.top: faceTitle.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Theme.spacingLg
        anchors.topMargin: Theme.spacingSm
        text: face.body
        color: Theme.textSecondary
        font.pixelSize: Theme.fontBody13
        wrapMode: Text.WordWrap
    }

    Label {
        id: finePrintLabel
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Theme.spacingLg
        anchors.bottomMargin: 14
        text: face.finePrint
        color: Theme.textTertiary
        font.pixelSize: Theme.fontMeta
    }
    Rectangle {
        id: hairline
        anchors.bottom: finePrintLabel.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.spacingLg
        anchors.rightMargin: Theme.spacingLg
        anchors.bottomMargin: 10
        height: 1
        color: Theme.border
    }
    Button {
        id: ghostBtn
        anchors.bottom: hairline.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.spacingLg
        anchors.rightMargin: Theme.spacingLg
        anchors.bottomMargin: Theme.spacingMd
        height: 32
        text: face.ghostText
        onClicked: face.ghostClicked()
        contentItem: Label {
            text: ghostBtn.text
            color: Theme.textSecondary
            font.pixelSize: Theme.fontControl
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: Theme.radiusSm
            color: ghostBtn.hovered ? Theme.hoverWash : "transparent"
            border.width: 1
            border.color: Theme.border
        }
    }
    Button {
        id: primaryBtn
        anchors.bottom: ghostBtn.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.spacingLg
        anchors.rightMargin: Theme.spacingLg
        anchors.bottomMargin: Theme.spacingSm
        height: 32
        text: face.primaryText
        // a phase with no primary action (e.g. the wizard's P3 benching) hides the button
        // rather than showing a blank clickable control; anchors keep their geometry so the
        // well above it never reflows.
        visible: face.primaryText !== ""
        onClicked: face.primaryClicked()
        contentItem: Label {
            text: primaryBtn.text
            color: Theme.onAccent
            font.pixelSize: Theme.fontControl
            font.weight: Theme.weightMedium
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: Theme.radiusSm
            color: primaryBtn.hovered ? Qt.lighter(face.primaryColor, 1.1)
                                      : face.primaryColor
        }
    }
    Rectangle {
        anchors.bottom: primaryBtn.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.spacingLg
        anchors.rightMargin: Theme.spacingLg
        anchors.bottomMargin: Theme.spacingMd
        height: 31
        radius: Theme.radiusSm
        color: Theme.surface
        visible: face.wellVisible
        Item {
            id: wellSlot
            anchors.fill: parent
            anchors.leftMargin: Theme.spacingMd
            anchors.rightMargin: Theme.spacingMd
        }
    }
}
