import QtQuick
import QtQuick.Controls.Basic
import "."

Popup {
    id: face

    property string title: ""
    property int count: 0
    property string subtitle: ""

    // the records law: the filter appears only past 10 rows
    property bool filterable: false
    property alias filterText: filterField.text
    property string filterPlaceholder: "Filter by name"

    default property alias content: contentSlot.data
    property alias footer: footerSlot.data

    function resetFilter() { filterField.text = "" }

    anchors.centerIn: parent
    // sec 3 modal guard: every face is already <=620 and none was width-responsive, so
    // below a 660 window they clamp to width-24 (12px side margins) instead of
    // overhanging. Theme.usableWidth excludes the rail; modals center on the WINDOW,
    // so the rail's 64px is added back before comparing.
    width: Math.min(620, Theme.usableWidth + 64 - 24)
    modal: true
    focus: true
    // 1, not 0: the background's 1px border is drawn INSIDE the popup's bounds, so a zero-padded
    // contentItem paints straight over it and the row dividers visibly cross the frame. Design's
    // render stops its dividers exactly 1px short of the frame (x 14..551 against a border at
    // x=552). One pixel of padding reproduces that.
    padding: 1
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle {
        radius: Theme.radiusMd
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderStrong
    }
    Overlay.modal: Rectangle { color: Theme.scrimHover }

    contentItem: Column {
        spacing: 0

        Item {
            width: parent.width
            height: 62
            Column {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Theme.spacingLg
                spacing: 3
                Row {
                    spacing: Theme.spacingXs
                    Label {
                        text: face.title
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontDeckName
                        font.weight: Theme.weightMedium
                    }
                    // the count is METADATA, a step down from the title - only the title is
                    // large text. Baseline-aligned so the smaller run still sits on the title's
                    // baseline rather than centring against it.
                    Label {
                        anchors.baseline: parent.children[0].baseline
                        text: "· " + face.count
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontControl
                    }
                }
                Label {
                    text: face.subtitle
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMeta
                }
            }
            Rectangle {
                id: closeBtn
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Theme.spacingLg
                width: 22; height: 22; radius: Theme.radiusSm
                color: closeHover.hovered ? Theme.hoverWash : "transparent"
                Item {
                    anchors.centerIn: parent
                    width: 11; height: 11
                    Rectangle { anchors.centerIn: parent; width: 13; height: 1.4
                                radius: 0.7; rotation: 45; color: Theme.textSecondary }
                    Rectangle { anchors.centerIn: parent; width: 13; height: 1.4
                                radius: 0.7; rotation: -45; color: Theme.textSecondary }
                }
                HoverHandler { id: closeHover }
                TapHandler { onTapped: face.close() }
            }
        }
        // structural rule under the header: neutral hairline .12, not the tinted border token
        Rectangle { width: parent.width; height: 1; color: Theme.hairline }

        Item {
            width: parent.width
            height: face.filterable ? 42 : 0
            visible: face.filterable
            clip: true
            Rectangle {
                anchors.fill: parent
                anchors.margins: Theme.spacingSm
                anchors.leftMargin: Theme.spacingLg
                anchors.rightMargin: Theme.spacingLg
                radius: Theme.radiusSm
                color: Theme.inputWell
                border.width: 1
                border.color: Theme.border
                TextInput {
                    id: filterField
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spacingSm
                    verticalAlignment: TextInput.AlignVCenter
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontControl
                    clip: true
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: filterField.text === ""
                        text: face.filterPlaceholder
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontControl
                    }
                }
            }
        }

        Item {
            id: contentSlot
            width: parent.width
            height: childrenRect.height
        }

        Rectangle {
            width: parent.width
            height: 1
            color: Theme.hairline
            visible: footerSlot.childrenRect.height > 0
        }
        Item {
            id: footerSlot
            width: parent.width
            height: childrenRect.height
        }
    }
}
