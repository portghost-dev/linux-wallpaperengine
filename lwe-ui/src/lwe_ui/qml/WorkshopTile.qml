import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import "."

// One Workshop tile (cluster redo 19a.4): library-card geometry (Workshop == Library, one
// component, no divergent sizing). The type pill is FIXED on the thumb bottom-left and never
// relocates on hover. At rest the title row carries ONE state chip: Missing dependency, or the
// records-sourced Crashed fact. Hover adds the center bench mark
// (shovel-pickaxe) + a corner gear (edit) + a corner trash - NO check (the wizard owns approval).
Rectangle {
    id: tile

    property string wid: ""
    property string title: ""
    property url thumb: ""
    property string wpType: ""
    property string forecast: ""
    property bool crashed: false
    property bool depMissing: false
    property real thumbHeight: 136
    readonly property int titleRowHeight: 34

    signal benchClicked(string id)   // shovel-pickaxe -> the import wizard / bench
    signal editClicked(string id)    // gear -> the editor for this wallpaper
    signal trashRequested(string id)
    signal depChipClicked(string id)

    implicitWidth: 224
    implicitHeight: thumbHeight + titleRowHeight + 2
    radius: Theme.radiusLg
    color: Theme.surface
    clip: true

    HoverHandler { id: hover }

    Item {
        id: thumbBox
        width: parent.width
        height: tile.thumbHeight

        Rectangle {
            anchors.fill: parent
            color: Theme.surfaceVariant
            topLeftRadius: Theme.radiusLg
            topRightRadius: Theme.radiusLg
        }
        Image {
            id: rawThumb
            anchors.fill: parent
            source: tile.thumb
            fillMode: Image.PreserveAspectCrop
            sourceSize.width: Theme.previewCap
            asynchronous: true
            cache: true
            visible: false
            layer.enabled: true
        }
        Item {
            id: thumbMask
            anchors.fill: parent
            visible: false
            layer.enabled: true
            Rectangle {
                anchors.fill: parent
                anchors.topMargin: 1
                anchors.leftMargin: 1
                anchors.rightMargin: 1
                color: "white"
                topLeftRadius: Theme.radiusLg - 1
                topRightRadius: Theme.radiusLg - 1
            }
        }
        MultiEffect {
            anchors.fill: parent
            source: rawThumb
            maskEnabled: true
            maskSource: thumbMask
            visible: tile.thumb.toString() !== "" && rawThumb.status === Image.Ready
        }
        Label {
            anchors.centerIn: parent
            visible: tile.thumb.toString() === ""
            text: tile.wpType !== "" ? tile.wpType : "no preview"
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMeta
        }

        Rectangle {
            anchors.fill: parent
            topLeftRadius: Theme.radiusLg
            topRightRadius: Theme.radiusLg
            color: Theme.scrimHover
            opacity: hover.hovered ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Motion.wake } }
        }

        Chip {
            anchors.left: parent.left
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spacingSm
            kind: "type"
            text: tile.wpType
            shell: Theme.scrimPlate
            visible: tile.wpType !== ""
        }

        Rectangle {
            anchors.centerIn: parent
            width: 46; height: 46; radius: 23
            color: Theme.accent
            opacity: hover.hovered ? 1 : 0
            visible: opacity > 0
            scale: hover.hovered ? 1 : 0.9
            Behavior on opacity { NumberAnimation { duration: Motion.wake } }
            Behavior on scale { NumberAnimation { duration: Motion.wake } }
            IconWorkshop {
                anchors.centerIn: parent
                size: 26
                color: Theme.onAccent
            }
            TapHandler { onTapped: tile.benchClicked(tile.wid) }
        }

        Rectangle {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme.spacingSm
            width: 24; height: 24; radius: Theme.radiusSm
            color: Theme.scrimPlate
            opacity: hover.hovered ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: Motion.wake } }
            IconGear {
                anchors.centerIn: parent
                size: 15
                color: Theme.textPrimary
            }
            TapHandler { onTapped: tile.editClicked(tile.wid) }
        }

        Rectangle {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spacingSm
            width: 24; height: 24; radius: Theme.radiusSm
            color: Theme.scrimPlate
            opacity: hover.hovered ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: Motion.wake } }
            IconTrash {
                anchors.centerIn: parent
                size: 15
                color: Theme.danger
            }
            TapHandler { onTapped: tile.trashRequested(tile.wid) }
        }
    }

    Item {
        anchors.top: thumbBox.bottom
        width: parent.width
        height: 34

        Label {
            id: titleLabel
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: Theme.spacingMd
            anchors.right: stateChip.visible ? stateChip.left : parent.right
            anchors.rightMargin: Theme.spacingSm
            text: tile.title
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody13
            elide: Text.ElideRight
            maximumLineCount: 1
        }

        Chip {
            id: stateChip
            anchors.right: parent.right
            anchors.rightMargin: Theme.spacingMd
            anchors.verticalCenter: parent.verticalCenter
            visible: tile.depMissing || tile.crashed || tile.forecast !== ""
            kind: tile.depMissing ? "fact" : tile.crashed ? "fact" : "forecast"
            tone: tile.depMissing ? Theme.warning : Theme.danger
            text: tile.depMissing ? "Missing dependency"
                : tile.crashed ? "Crashed"
                : tile.forecast

            TapHandler {
                enabled: tile.depMissing
                onTapped: tile.depChipClicked(tile.wid)
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: "transparent"
        radius: tile.radius
        border.width: 1
        border.color: hover.hovered ? Theme.borderStrong : Theme.border
    }
}
