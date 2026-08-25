import QtQuick
import QtQuick.Controls.Basic
import "."

Item {
    id: seg

    property var model: []
    property int currentIndex: 0
    signal activated(int index)

    property string sizeClass: "h26"

    readonly property var _sizeClasses: ({
        "h28": {"h": 28, "pad": 12, "font": 12},
        "h26": {"h": 26, "pad": 11, "font": 12},
        "h24": {"h": 24, "pad": 10, "font": 11},
        "h22": {"h": 22, "pad": 8,  "font": 11}
    })
    readonly property var _cls: _sizeClasses[sizeClass] || _sizeClasses["h26"]

    readonly property int cellPadH: _cls.pad
    readonly property int cellHeight: _cls.h
    readonly property int fontPx: _cls.font

    implicitWidth: row.implicitWidth + 2      // + the 1px outer border on each side
    implicitHeight: cellHeight
    width: implicitWidth
    height: implicitHeight

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSm
        color: "transparent"
        border.width: 1
        border.color: Theme.border
        clip: true

        Row {
            id: row
            anchors.fill: parent
            anchors.margins: 1

            Repeater {
                model: seg.model
                delegate: Item {
                    id: cell
                    required property var modelData
                    required property int index
                    readonly property bool current: seg.currentIndex === index

                    width: cellLabel.implicitWidth + seg.cellPadH * 2
                    height: row.height

                    Rectangle {
                        visible: cell.index > 0
                        width: 1
                        height: parent.height
                        anchors.left: parent.left
                        color: Theme.border
                    }

                    Rectangle {
                        anchors.fill: parent
                        color: cell.current ? Theme.segmentWash : "transparent"
                    }

                    Label {
                        id: cellLabel
                        anchors.centerIn: parent
                        text: cell.modelData
                        font.pixelSize: seg.fontPx
                        color: cell.current ? Theme.textPrimary : Theme.textSecondary
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: seg.activated(cell.index)
                    }
                }
            }
        }
    }
}
