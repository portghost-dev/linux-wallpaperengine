import QtQuick
import QtQuick.Controls.Basic
import "."

Rectangle {
    id: view
    objectName: "settingsView"

    signal closed()

    color: Theme.base

    // font sizes are declared as REAL properties, not literals: Qt's
    // font.pixelSize is an int property and a fractional literal is a type
    // error, while a real-typed binding converts. The design system already
    // does this (Theme.fontMicro is a real).
    readonly property real fontBanner: 11.5

    readonly property int scrollbarClearance: 16
    readonly property int scrollbarInset: 3
    readonly property int scrollbarHandle: 4

    // The Theme page's six role rows carry a selection wash that deliberately bleeds
    // Theme.spacingSm PAST the row on both sides, so the row text stays aligned while the
    // wash reads as a full-width band (SettingsTheme.qml:143-146 - a frozen surface, and the
    // bleed is intentional design, not a bug to fix there). That overhang is content as far
    // as the clearance law is concerned, so the page is given exactly that much less width
    // and the wash lands ON the content edge instead of under the scrollbar.
    // Measured: without this the Theme page cleared the handle by 1px, not 9.
    readonly property int pageBleed: view.pageIndex === 3 ? Theme.spacingSm : 0

    property int pageIndex: 0
    readonly property var pageNames: ["General", "Engine", "Library", "Theme"]

    // --- failure grammar (S5) ------------------------------------------------------------
    // Every failed commit raises the banner AND outlines the control that failed, for the
    // same 2500ms. Success is silent. Silent failure is illegal on this surface, which is
    // what the old world was made of: setSetting returns void and swallows exceptions.
    property var failedKeys: []
    property string failReason: ""
    function isFailed(key) { return key !== "" && view.failedKeys.indexOf(key) >= 0 }

    Connections {
        target: settingsBridge
        function onCommitFailed(keys, reason) {
            view.failedKeys = keys;
            view.failReason = reason;
            failClear.restart();
        }
    }
    Timer {
        id: failClear
        interval: 2500
        onTriggered: { view.failedKeys = []; view.failReason = ""; }
    }

    Item {
        readonly property int groupW: 28 + 640 + 28
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        width: parent.width > 1400 ? Math.min(groupW, parent.width) : parent.width

        Item {
            id: contentBox
            objectName: "settingsContentColumn"
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.topMargin: 0
            anchors.bottomMargin: Theme.spacingLg
            anchors.leftMargin: 28
            width: Math.max(0, Math.min(640, parent.width - 28 * 2))

            Item { id: pinnedTop; width: 1; height: 18 }

            SegmentControl {
                id: pageSeg
                objectName: "settingsPageSegment"
                anchors.top: pinnedTop.bottom
                sizeClass: "h24"
                model: view.pageNames
                currentIndex: view.pageIndex
                onActivated: function(i) { view.pageIndex = i; }
            }

            Item {
                id: bannerBox
                anchors.top: pageSeg.bottom
                width: contentBox.width - view.scrollbarClearance
                height: banner.visible ? banner.height + 10 : 0
                Rectangle {
                    id: banner
                    objectName: "settingsFailBanner"
                    visible: view.failedKeys.length > 0
                    y: 10
                    width: parent.width
                    height: bannerLabel.implicitHeight + 14
                    radius: 6
                    color: Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.12)
                    border.width: 1
                    border.color: Qt.rgba(Theme.danger.r, Theme.danger.g,
                                          Theme.danger.b, 0.45)
                    Label {
                        id: bannerLabel
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10
                        text: view.failReason === ""
                              ? "Change Failed"
                              : "Change Failed. " + view.failReason
                        color: "#F2A0A3"
                        font.pixelSize: view.fontBanner
                        wrapMode: Text.WordWrap
                    }
                }
            }

            Flickable {
                id: pageFlick
                anchors.top: bannerBox.bottom
                anchors.topMargin: 14
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                contentHeight: shellCol.height
                clip: true

                ScrollBar.vertical: ScrollBar {
                    id: settingsBar
                    objectName: "settingsScrollBar"
                    policy: ScrollBar.AsNeeded
                    // An attached ScrollBar positions its own x, so the 3px inset is bought
                    // with padding rather than a coordinate the control overwrites: the bar
                    // is 7 wide with 3 of right padding, leaving a 4px handle riding 3px
                    // inside the content column's right edge. Reserves zero layout width -
                    // it overlays the Flickable, it does not narrow it.
                    padding: 0
                    rightPadding: 3
                    width: 7
                    contentItem: Rectangle {
                        implicitWidth: 4
                        radius: 2
                        color: Qt.rgba(1, 1, 1, 0.25)
                        opacity: settingsBar.active ? 1.0 : 0.0
                        Behavior on opacity { NumberAnimation { duration: 200 } }
                    }
                    background: null
                }

                Column {
                    id: shellCol
                    objectName: "settingsShellColumn"
                    width: parent.width - view.scrollbarClearance
                    spacing: 0

                    Loader {
                        id: pageLoader
                        width: parent.width - view.pageBleed
                        source: ["SettingsGeneral.qml", "SettingsEngine.qml",
                                 "SettingsLibrary.qml", "SettingsTheme.qml"][view.pageIndex]
                    }
                }
            }
        }
    }
}
