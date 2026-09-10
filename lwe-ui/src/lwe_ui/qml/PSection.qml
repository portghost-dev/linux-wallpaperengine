import QtQuick
import QtQuick.Controls.Basic
import "."

// PSection - the settings-tier section header: a page is scanned by its headers, so the
// header outranks its hairline (PRule stays the instrument tier). The first header on a
// page takes no top margin because the shell already sets it 14 under the tab strip.
Item {
    id: psec

    property string label: ""
    property bool first: false

    readonly property int topMargin: first ? 0 : 26
    readonly property int bottomMargin: 6

    width: parent ? parent.width : 0
    height: topMargin + headerLabel.implicitHeight + bottomMargin

    Label {
        id: headerLabel
        anchors.left: parent.left
        y: psec.topMargin
        text: psec.label
        color: Theme.textPrimary
        font.pixelSize: Theme.fontBody13
        font.weight: Theme.weightMedium
    }
    Rectangle {
        anchors.left: headerLabel.right
        anchors.leftMargin: 8
        anchors.right: parent.right
        anchors.verticalCenter: headerLabel.verticalCenter
        height: 1
        color: Theme.hairlineSection
    }
}
