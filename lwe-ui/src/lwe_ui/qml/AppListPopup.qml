import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Effects
import "."

Popup {
    id: pop

    property string kind: "exceptions"        // "exceptions" | "apps"
    readonly property bool forApps: kind === "apps"
    readonly property string titleText: forApps ? "Apps" : "Exceptions"
    readonly property string captionText: forApps
        ? "The rule applies while any of these is open"
        : "These apps never trigger the rule"

    property var entries: []
    property var runningRows: []
    readonly property real fontEntry: 11.5

    width: 340
    topPadding: 14
    bottomPadding: 14
    leftPadding: 16
    rightPadding: 16
    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    Overlay.modal: Rectangle { color: Theme.scrimHover }
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    // content-driven up to window-minus-margins; past that the list region scrolls
    readonly property real maxH: (Overlay.overlay ? Overlay.overlay.height : 620) - 24
    implicitHeight: Math.min(topPadding + bottomPadding + body.naturalHeight, maxH)

    onOpened: refresh()

    function refresh() {
        entries = forApps ? settingsBridge.appEntries() : settingsBridge.exceptions();
        runningRows = settingsBridge.runningNow(kind);
    }
    Timer {
        interval: 3000
        running: pop.visible
        repeat: true
        onTriggered: pop.runningRows = settingsBridge.runningNow(pop.kind)
    }

    function tryAdd(raw) {
        var name = String(raw).trim();
        if (pop.forApps)
            name = name.slice(0, 15);      // comm limit; mirrors the bridge's own truncation
        if (name === "")
            return;
        if (pop.entries.indexOf(name) >= 0) {
            dupPulse.restart();
            return;
        }
        var ok = pop.forApps ? settingsBridge.addAppEntry(name)
                             : settingsBridge.addException(name);
        if (ok) {
            addField.text = "";
            pop.refresh();
        }
    }
    function removeEntry(name) {
        var ok = pop.forApps ? settingsBridge.removeAppEntry(name)
                             : settingsBridge.removeException(name);
        if (ok)
            pop.refresh();
    }
    Timer { id: dupPulse; interval: 2500 }

    background: Rectangle {
        color: Theme.surface
        radius: 8
        border.width: 1
        border.color: Theme.borderStrong
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: "#000000"
            shadowOpacity: 0.5
            blurMax: 20
            shadowBlur: 1.0
            shadowVerticalOffset: 6
        }
    }

    contentItem: Item {
        id: body
        // what the popup WOULD need with nothing scrolling; the cap above may shrink it,
        // and the difference comes out of the scroll region alone
        readonly property real naturalHeight: header.height + 10 + listCol.implicitHeight
                                              + 12 + addRow.height

        Item {
            id: header
            anchors.left: parent.left
            anchors.right: parent.right
            height: titleLabel.implicitHeight + capLabel.implicitHeight + 2

            Label {
                id: titleLabel
                anchors.left: parent.left
                text: pop.titleText
                color: Theme.textPrimary
                font.pixelSize: 14
                font.weight: Theme.weightMedium
            }
            Label {
                id: capLabel
                anchors.left: parent.left
                anchors.top: titleLabel.bottom
                anchors.topMargin: 2
                text: pop.captionText
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMicro
            }
            Item {
                width: 20
                height: 20
                anchors.right: parent.right
                anchors.top: parent.top
                Label {
                    anchors.centerIn: parent
                    text: "\u00d7"
                    color: closeHover.hovered ? Theme.textPrimary : Theme.textSecondary
                    font.pixelSize: 15
                }
                HoverHandler { id: closeHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: pop.close() }
            }
        }

        // --- scroll region: current entries + running now ---------------------------------
        // DECK-POPUP SCROLLBAR MECHANISM (DEFECT-2 class, the recurring one): the
        // viewport BLEEDS into the card's right padding so the overlay bar rides out
        // there, near the card edge, and the rows keep their full content width under
        // it. Without the bleed the bar overlays the remove/add glyphs at the content
        // edge - the exact defect that has now recurred across surfaces five times.
        readonly property real barBleed: pop.rightPadding - 1

        Flickable {
            id: listFlick
            anchors.top: header.bottom
            anchors.topMargin: 10
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.rightMargin: -body.barBleed
            anchors.bottom: addRow.top
            anchors.bottomMargin: 12
            contentHeight: listCol.implicitHeight
            clip: true
            interactive: contentHeight > height

            ScrollBar.vertical: ScrollBar {
                id: listBar
                policy: ScrollBar.AsNeeded
                padding: 0
                rightPadding: 3
                width: 7
                contentItem: Rectangle {
                    implicitWidth: 4
                    radius: 2
                    color: Qt.rgba(1, 1, 1, 0.25)
                    opacity: listBar.active ? 1.0 : 0.0
                    Behavior on opacity { NumberAnimation { duration: 200 } }
                }
                background: null
            }

            Column {
                id: listCol
                // the bleed belongs to the BAR, not the rows: rows stay at content width
                width: listFlick.width - body.barBleed
                spacing: 0

                Repeater {
                    model: pop.entries
                    delegate: Item {
                        required property string modelData
                        required property int index
                        width: listCol.width
                        height: 32
                        Rectangle {
                            visible: index > 0
                            anchors.top: parent.top
                            width: parent.width
                            height: 1
                            color: Qt.rgba(1, 1, 1, 0.08)
                        }
                        Label {
                            anchors.left: parent.left
                            anchors.right: removeGlyph.left
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData
                            color: Theme.textPrimary
                            font.family: Theme.monoFamily
                            font.pixelSize: pop.fontEntry
                            elide: Text.ElideRight
                        }
                        Item {
                            id: removeGlyph
                            width: 16
                            height: 16
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            Label {
                                anchors.centerIn: parent
                                text: "\u00d7"
                                color: removeHover.hovered ? Theme.textPrimary
                                                           : Theme.textTertiary
                                font.pixelSize: 13
                            }
                            HoverHandler { id: removeHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: pop.removeEntry(modelData) }
                        }
                    }
                }

                // Running now - absent entirely when the source has nothing (a non-Hyprland
                // session on the exceptions list, or /proc unreadable)
                Item {
                    visible: pop.runningRows.length > 0
                    width: listCol.width
                    height: 24
                    Label {
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        text: "Running now"
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontMeta
                        font.weight: Theme.weightMedium
                    }
                    Label {
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 1
                        text: "click to add"
                        color: Theme.textTertiary
                        font.pixelSize: 10
                    }
                }
                Repeater {
                    model: pop.runningRows
                    delegate: Item {
                        id: runRow
                        required property var modelData
                        required property int index
                        readonly property bool present:
                            pop.entries.indexOf(String(modelData.match)) >= 0
                        width: listCol.width
                        height: 30
                        opacity: present ? 0.4 : 1.0
                        Rectangle {
                            visible: index > 0
                            anchors.top: parent.top
                            width: parent.width
                            height: 1
                            color: Qt.rgba(1, 1, 1, 0.08)
                        }
                        Label {
                            id: humanLabel
                            anchors.left: parent.left
                            anchors.right: matchLabel.left
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: String(runRow.modelData.human)
                            color: Theme.textPrimary
                            font.pixelSize: pop.fontEntry
                            elide: Text.ElideRight
                        }
                        Label {
                            id: matchLabel
                            anchors.right: plusGlyph.left
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            visible: !pop.forApps
                            text: String(runRow.modelData.match)
                            color: Theme.textTertiary
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fontMicro
                        }
                        Label {
                            id: plusGlyph
                            width: 16
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            horizontalAlignment: Text.AlignHCenter
                            text: "+"
                            color: Theme.textSecondary
                            font.pixelSize: 14
                            visible: !runRow.present
                        }
                        HoverHandler {
                            enabled: !runRow.present
                            cursorShape: Qt.PointingHandCursor
                        }
                        TapHandler {
                            enabled: !runRow.present
                            onTapped: pop.tryAdd(String(runRow.modelData.match))
                        }
                    }
                }
            }
        }

        Item {
            id: addRow
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 26

            Rectangle {
                id: fieldBox
                anchors.left: parent.left
                anchors.right: pop.forApps ? browseBtn.left : addBtn.left
                anchors.rightMargin: 8
                height: 26
                radius: Theme.radiusSm
                color: Theme.inputWell
                border.width: dupPulse.running ? 1.5 : 1
                border.color: dupPulse.running
                    ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.9)
                    : Theme.border
                TextInput {
                    id: addField
                    anchors.fill: parent
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    verticalAlignment: Text.AlignVCenter
                    color: Theme.textPrimary
                    font.family: Theme.monoFamily
                    font.pixelSize: pop.fontEntry
                    selectByMouse: true
                    clip: true
                    Label {
                        anchors.fill: parent
                        verticalAlignment: Text.AlignVCenter
                        visible: addField.text === "" && !addField.activeFocus
                        text: pop.forApps ? "Process name" : "App id"
                        color: Theme.textTertiary
                        font.family: Theme.monoFamily
                        font.pixelSize: pop.fontEntry
                    }
                    Keys.onReturnPressed: pop.tryAdd(addField.text)
                    Keys.onEnterPressed: pop.tryAdd(addField.text)
                }
            }

            Rectangle {
                id: browseBtn
                // Browse is an APPS-ONLY door [S-18]: a file picker can resolve to a comm
                // name, never to a window class
                visible: pop.forApps
                anchors.right: addBtn.left
                anchors.rightMargin: 8
                width: browseLabel.implicitWidth + 20
                height: 26
                radius: Theme.radiusSm
                color: "transparent"
                border.width: 1
                border.color: Theme.borderStrong
                Label {
                    id: browseLabel
                    anchors.centerIn: parent
                    text: "Browse"
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontMeta
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: browseDialog.open() }
            }

            Rectangle {
                id: addBtn
                anchors.right: parent.right
                width: addLabel.implicitWidth + 20
                height: 26
                radius: Theme.radiusSm
                color: "transparent"
                border.width: 1
                border.color: Theme.borderStrong
                Label {
                    id: addLabel
                    anchors.centerIn: parent
                    text: "Add"
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontMeta
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: pop.tryAdd(addField.text) }
            }
        }
    }

    FileDialog {
        id: browseDialog
        // H-A2 [S-18]: the picked file resolves to its basename cut to the comm limit,
        // SHOWN in the field rather than committed - the user sees exactly what will be
        // stored before Add
        onAccepted: {
            var s = String(selectedFile);
            var base = s.substring(s.lastIndexOf("/") + 1);
            addField.text = base.slice(0, 15);
            addField.forceActiveFocus();
        }
    }
}
