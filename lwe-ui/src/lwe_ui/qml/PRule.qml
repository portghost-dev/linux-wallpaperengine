import QtQuick
import QtQuick.Controls.Basic
import "."

Item {
    id: prule

    property string label: ""

    width: parent ? parent.width : 0
    height: 24

    Label {
        id: ruleLabel
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: prule.label
        color: Theme.textSecondary
        font.pixelSize: Theme.fontMeta
        font.weight: Theme.weightMedium
    }
    Rectangle {
        anchors.left: ruleLabel.right
        anchors.leftMargin: 8
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        height: 1
        color: Theme.border
    }
}
