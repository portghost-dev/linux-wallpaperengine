import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import "."

// One exhibit slot card: side chip + state, scene, binary, label, and the last exit code.
Rectangle {
    id: card

    property string side: "A"
    property int rev: 0

    readonly property var st: (rev, dev.slotState(card.side))

    color: Theme.surface
    radius: Theme.radiusMd
    border.width: 1
    border.color: Theme.hairline
    implicitHeight: content.implicitHeight + 20
    height: implicitHeight

    Column {
        id: content
        anchors.fill: parent
        anchors.margins: 10
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        spacing: 0

        Item {
            width: parent.width
            height: 17
            Rectangle {
                id: chip
                width: 17; height: 17; radius: 4
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                color: card.side === "A" ? Theme.accent : "transparent"
                border.width: card.side === "A" ? 0 : 1
                border.color: Theme.hairlineStrong
                Label {
                    anchors.centerIn: parent
                    text: card.side
                    font.pixelSize: 10
                    font.weight: Theme.weightMedium
                    color: card.side === "A" ? Theme.onAccent : Theme.textPrimary
                }
            }
            Label {
                objectName: "devSlotState"
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: card.st.state || ""
                font.pixelSize: 10
                color: card.st.relaunching === true ? Theme.warning
                     : (card.st.lastCode > 0 && card.st.alive !== true && card.st.lastStopped !== true) ? Theme.danger
                     : Theme.textTertiary
            }
        }

        Item { width: 1; height: 7 }

        Rectangle {
            id: sceneDrop
            width: parent.width
            height: 25
            radius: Theme.radiusSm
            color: Theme.inputWell
            border.width: 1
            border.color: Theme.border
            Label {
                anchors.left: parent.left
                anchors.leftMargin: 10
                anchors.right: sceneCaret.left
                anchors.verticalCenter: parent.verticalCenter
                text: card.st.sceneTitle || "Scene"
                textFormat: Text.PlainText
                elide: Text.ElideRight
                font.pixelSize: 11
                color: (card.st.scene || "") !== "" ? Theme.textPrimary : Theme.textTertiary
            }
            IconChevron {
                id: sceneCaret
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                direction: "down"
                size: 10
                color: Theme.textSecondary
            }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: scenePop.visible ? scenePop.close() : scenePop.open() }

            Popup {
                id: scenePop
                objectName: "devScenePopup"
                y: sceneDrop.height + 2
                width: sceneDrop.width
                implicitHeight: Math.min(sceneList.contentHeight + 2, 260)
                padding: 1
                closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                background: Rectangle {
                    radius: Theme.radiusSm
                    color: Theme.surfaceVariant
                    border.width: 1
                    border.color: Theme.borderStrong
                    // a press anywhere on the popup takes the exclusive grab: a passive one
                    // travels on to the fields the popup covers
                    TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds }
                }
                onAboutToShow: sceneList.model = scenePop.entries()

                function entries() {
                    var out = [];
                    var all = dev.sceneChoices();
                    var probes = 0;
                    for (var i = 0; i < all.length; i++) {
                        if (all[i].section === "probes") probes++;
                        else out.push({kind: "scene", wid: all[i].wid, title: all[i].title});
                    }
                    out.push({kind: "rule"});
                    if (probes === 0) {
                        out.push({kind: "empty"});
                    } else {
                        for (var j = 0; j < all.length; j++)
                            if (all[j].section === "probes")
                                out.push({kind: "scene", wid: all[j].wid, title: all[j].title});
                    }
                    return out;
                }

                contentItem: ListView {
                    id: sceneList
                    objectName: "devSceneList"
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Item {
                        id: sceneRow
                        required property var modelData
                        width: sceneList.width
                        height: modelData.kind === "rule" ? 5 : 24
                        Rectangle {
                            visible: sceneRow.modelData.kind === "rule"
                            anchors.centerIn: parent
                            width: parent.width - 16
                            height: 1
                            color: Theme.border
                        }
                        Rectangle {
                            visible: sceneRow.modelData.kind !== "rule"
                            anchors.fill: parent
                            color: sceneHover.hovered && sceneRow.modelData.kind === "scene" ? Theme.hoverWash : "transparent"
                            radius: Theme.radiusXs
                            Label {
                                anchors.left: parent.left
                                anchors.leftMargin: 10
                                anchors.right: parent.right
                                anchors.rightMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                text: sceneRow.modelData.kind === "empty" ? "No probes" : (sceneRow.modelData.title || "")
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                font.pixelSize: 11
                                color: sceneRow.modelData.kind === "empty" ? Theme.textTertiary : Theme.textPrimary
                            }
                            HoverHandler { id: sceneHover; enabled: sceneRow.modelData.kind === "scene" }
                            TapHandler {
                                enabled: sceneRow.modelData.kind === "scene"
                                gesturePolicy: TapHandler.ReleaseWithinBounds
                                onTapped: { dev.setScene(card.side, sceneRow.modelData.wid); scenePop.close(); }
                            }
                        }
                    }
                }
            }
        }

        Item { width: 1; height: 6 }

        Item {
            width: parent.width
            height: 23
            Label {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: 42
                text: "Binary"
                font.pixelSize: 10
                color: Theme.textTertiary
            }
            Rectangle {
                id: binDrop
                objectName: "devBinaryField"
                anchors.left: parent.left
                anchors.leftMargin: 42
                anchors.right: parent.right
                height: 23
                radius: 5
                color: Theme.inputWell
                border.width: 1
                border.color: Theme.border
                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    anchors.right: binCaret.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: card.st.binaryLabel || "Same as daemon"
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.pixelSize: 10
                    font.family: Theme.monoFamily
                    color: (card.st.binary || "") !== "" ? Theme.textPrimary : Theme.textTertiary
                }
                IconChevron {
                    id: binCaret
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    direction: "down"
                    size: 10
                    color: Theme.textSecondary
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        if (binMenu.visible) { binMenu.close(); return; }
                        binMenu.choices = dev.binaryChoices();
                        binMenu.open();
                    }
                }
                Menu {
                    id: binMenu
                    objectName: "devBinaryMenu"
                    property var choices: []
                    parent: binDrop
                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                    y: binDrop.height + 2
                    width: binDrop.width
                    background: Rectangle {
                        color: Theme.surfaceVariant
                        radius: Theme.radiusSm
                        border.width: 1
                        border.color: Theme.borderStrong
                    }
                    Repeater {
                        model: binMenu.choices
                        delegate: ThemedMenuItem {
                            required property var modelData
                            text: modelData.label
                            onTriggered: dev.setBinary(card.side, modelData.value)
                        }
                    }
                    ThemedMenuItem {
                        text: "Browse…"
                        onTriggered: binDialog.open()
                    }
                }
                FileDialog {
                    id: binDialog
                    title: "Binary"
                    currentFolder: "file://" + dev.binariesDir()
                    onAccepted: dev.setBinary(card.side, decodeURIComponent(String(selectedFile).replace(/^file:\/\//, "")))
                }
            }
        }

        Item { width: 1; height: 6 }

        Item {
            width: parent.width
            height: 23
            Label {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: 42
                text: "Label"
                font.pixelSize: 10
                color: Theme.textTertiary
            }
            TextField {
                id: labelField
                objectName: "devSlotLabel"
                anchors.left: parent.left
                anchors.leftMargin: 42
                anchors.right: parent.right
                height: 23
                text: card.st.label || card.side
                color: Theme.textPrimary
                font.pixelSize: 10
                font.family: Theme.monoFamily
                selectByMouse: true
                leftPadding: 8
                background: Rectangle {
                    radius: 5
                    color: Theme.inputWell
                    border.width: 1
                    border.color: labelField.activeFocus ? Theme.borderStrong : Theme.border
                }
                onEditingFinished: dev.setLabel(card.side, text)
            }
        }

        Item { width: 1; height: 6 }

        Item {
            width: parent.width
            height: 23
            Label {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                width: 42
                text: "Stats"
                font.pixelSize: 10
                color: Theme.textTertiary
            }
            ThemedSwitch {
                id: statsSwitch
                objectName: "devSlotStats"
                anchors.left: parent.left
                anchors.leftMargin: 42
                anchors.verticalCenter: parent.verticalCenter
                pillWidth: 26
                pillHeight: 15
                enabled: card.st.legacy !== true
                opacity: enabled ? 1 : 0.5
                checked: card.st.overlayStats === true
                onToggled: dev.setOverlayStats(card.side, checked)
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: "Draws FPS, CPU, RAM, VRAM and GPU on the exhibit in place of its label. Applies live."
            }
            Rectangle {
                id: cornerDrop
                objectName: "devSlotCorner"
                anchors.left: statsSwitch.right
                anchors.leftMargin: 8
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                height: 23
                radius: 5
                color: Theme.inputWell
                border.width: 1
                border.color: Theme.border
                enabled: card.st.legacy !== true
                opacity: enabled ? 1 : 0.5
                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    anchors.right: cornerCaret.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: card.st.overlayCornerLabel || "Top left"
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.pixelSize: 10
                    font.family: Theme.monoFamily
                    color: Theme.textPrimary
                }
                IconChevron {
                    id: cornerCaret
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    direction: "down"
                    size: 10
                    color: Theme.textSecondary
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        if (cornerMenu.visible) { cornerMenu.close(); return; }
                        cornerMenu.choices = dev.overlayCorners();
                        cornerMenu.open();
                    }
                }
                Menu {
                    id: cornerMenu
                    property var choices: []
                    parent: cornerDrop
                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                    y: cornerDrop.height + 2
                    width: cornerDrop.width
                    background: Rectangle {
                        color: Theme.surfaceVariant
                        radius: Theme.radiusSm
                        border.width: 1
                        border.color: Theme.borderStrong
                    }
                    Repeater {
                        model: cornerMenu.choices
                        delegate: ThemedMenuItem {
                            required property var modelData
                            text: modelData.label
                            onTriggered: dev.setOverlayCorner(card.side, modelData.value)
                        }
                    }
                }
            }
        }

        Item {
            // a failed run keeps its exit code in view; a clean one shows nothing
            readonly property bool shown: card.st.legacy === true || (card.st.lastCode || 0) > 0
            width: parent.width
            height: shown ? 27 : 0
            visible: shown
            Label {
                objectName: "devSlotResidue"
                anchors.left: parent.left
                anchors.right: tailButton.visible ? tailButton.left : parent.right
                anchors.rightMargin: tailButton.visible ? 8 : 0
                anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
                font.pixelSize: 10
                textFormat: Text.StyledText
                text: {
                    if (card.st.legacy === true)
                        return "Legacy: no live control";
                    var code = card.st.lastCode;
                    if (code === undefined || code < 0)
                        return "";
                    var n = code !== 0 ? "<font color=\"" + Theme.danger + "\">" + code + "</font>" : String(code);
                    return "Last run · exit " + n;
                }
                color: card.st.legacy === true ? Theme.warning : Theme.textTertiary
            }
            // Tail opens the retained run in the console, pinned to this side
            Rectangle {
                id: tailButton
                objectName: "devSlotTail"
                visible: card.st.hasResidue === true && (card.st.lastCode || 0) > 0
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                height: 20
                width: tailLabel.implicitWidth + 16
                radius: 5
                color: "transparent"
                border.width: 1
                border.color: Theme.hairlineStrong
                Label {
                    id: tailLabel
                    anchors.centerIn: parent
                    text: "Tail"
                    font.pixelSize: 10
                    color: Theme.textPrimary
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: dev.showTail(card.side) }
            }
        }
    }
}
