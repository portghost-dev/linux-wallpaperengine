import QtQuick
import QtQuick.Controls.Basic
import "."

// Raw env door: per-side env lines and property queue, the composed launch line, and the
// searchable reference of value-class knobs. Edits apply when a field loses focus.
Popup {
    id: door

    property int rev: 0
    property string search: ""

    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    anchors.centerIn: parent
    width: Math.min(920, (parent ? parent.width : 920) - 60)
    height: Math.min(600, (parent ? parent.height : 600) - 60)
    padding: 18

    background: Rectangle {
        color: Theme.surface
        radius: Theme.radiusLg
        border.width: 1
        border.color: Theme.borderStrong
    }

    onOpened: {
        envA.text = dev.envText("A"); envB.text = dev.envText("B");
        propA.text = dev.propText("A"); propB.text = dev.propText("B");
    }

    function applyAll() {
        dev.setEnvText("A", envA.text); dev.setEnvText("B", envB.text);
        dev.setPropText("A", propA.text); dev.setPropText("B", propB.text);
    }
    onClosed: applyAll()

    component Editor: Rectangle {
        id: ed
        property alias text: area.text
        property string side: "A"
        property bool props: false
        color: Theme.inputWell
        radius: Theme.radiusSm
        border.width: 1
        border.color: area.activeFocus ? Theme.borderStrong : Theme.border
        Flickable {
            anchors.fill: parent
            anchors.margins: 1
            clip: true
            contentHeight: area.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            TextArea.flickable: TextArea {
                id: area
                color: Theme.textPrimary
                font.pixelSize: Theme.fontMicro
                font.family: Theme.monoFamily
                selectByMouse: true
                wrapMode: TextEdit.NoWrap
                background: Item {}
                onActiveFocusChanged: {
                    if (activeFocus) return;
                    if (ed.props) dev.setPropText(ed.side, text);
                    else dev.setEnvText(ed.side, text);
                }
            }
            ScrollBar.vertical: ScrollBar {}
        }
    }

    component SideColumn: Column {
        id: sideCol
        property string side: "A"
        property alias envArea: envEd
        property alias propArea: propEd
        spacing: 6
        Row {
            spacing: 8
            Rectangle {
                width: 17; height: 17; radius: 4
                color: sideCol.side === "A" ? Theme.accent : "transparent"
                border.width: sideCol.side === "A" ? 0 : 1
                border.color: Theme.hairlineStrong
                Label {
                    anchors.centerIn: parent
                    text: sideCol.side
                    font.pixelSize: 10
                    font.weight: Theme.weightMedium
                    color: sideCol.side === "A" ? Theme.onAccent : Theme.textPrimary
                }
            }
            Label {
                anchors.verticalCenter: parent.verticalCenter
                text: "Raw env"
                font.pixelSize: 11
                font.weight: Theme.weightMedium
                color: Theme.textSecondary
            }
        }
        Editor { id: envEd; side: sideCol.side; width: sideCol.width; height: 110 }
        Label {
            text: "Properties"
            font.pixelSize: 11
            font.weight: Theme.weightMedium
            color: Theme.textSecondary
        }
        Editor { id: propEd; side: sideCol.side; props: true; width: sideCol.width; height: 64 }
        Label {
            width: sideCol.width
            text: (door.rev, door.visible ? dev.launchPreview(sideCol.side) : "")
            textFormat: Text.PlainText
            wrapMode: Text.WrapAnywhere
            maximumLineCount: 4
            elide: Text.ElideRight
            font.pixelSize: Theme.fontMicro - 1
            font.family: Theme.monoFamily
            color: Theme.textTertiary
        }
    }

    contentItem: Column {
        spacing: 12

        Row {
            id: sides
            width: parent.width
            spacing: 18
            SideColumn { id: colA; side: "A"; width: (sides.width - sides.spacing) / 2 }
            SideColumn { id: colB; side: "B"; width: (sides.width - sides.spacing) / 2 }
        }

        Item {
            width: parent.width
            height: 24
            Label {
                id: refLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "Reference"
                font.pixelSize: 11
                font.weight: Theme.weightMedium
                color: Theme.textSecondary
            }
            Rectangle {
                anchors.left: refLabel.right
                anchors.leftMargin: 8
                anchors.right: refSearch.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                height: 1
                color: Theme.hairline
            }
            TextField {
                id: refSearch
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                width: 180
                height: 22
                placeholderText: "search"
                placeholderTextColor: Theme.textTertiary
                color: Theme.textPrimary
                font.pixelSize: Theme.fontMicro
                font.family: Theme.monoFamily
                leftPadding: 8
                selectByMouse: true
                background: Rectangle {
                    radius: 5
                    color: Theme.inputWell
                    border.width: 1
                    border.color: refSearch.activeFocus ? Theme.borderStrong : Theme.border
                }
                onTextEdited: door.search = text.toLowerCase()
            }
        }

        ListView {
            id: refList
            width: parent.width
            height: Math.max(0, parent.height - y)
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            model: {
                door.rev;
                var all = dev.rawEnvReference();
                if (door.search === "") return all;
                var out = [];
                for (var i = 0; i < all.length; i++) {
                    var hay = (all[i].env + " " + all[i].what + " " + all[i].prints).toLowerCase();
                    if (hay.indexOf(door.search) >= 0) out.push(all[i]);
                }
                return out;
            }
            delegate: Item {
                id: refRow
                required property var modelData
                width: refList.width
                height: 26
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hairlineFaint }
                Label {
                    id: refEnv
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    width: 200
                    text: refRow.modelData.env
                    font.pixelSize: Theme.fontMicro
                    font.family: Theme.monoFamily
                    color: Theme.textPrimary
                    elide: Text.ElideRight
                }
                Label {
                    anchors.left: refEnv.right
                    anchors.leftMargin: 12
                    anchors.right: refPrints.left
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: refRow.modelData.what
                    font.pixelSize: Theme.fontMicro
                    color: Theme.textSecondary
                    elide: Text.ElideRight
                }
                Label {
                    id: refPrints
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 160
                    horizontalAlignment: Text.AlignRight
                    text: refRow.modelData.prints
                    font.pixelSize: 10
                    font.family: Theme.monoFamily
                    color: Theme.textTertiary
                    elide: Text.ElideRight
                }
            }
        }
    }

    property alias envA: colA.envArea
    property alias envB: colB.envArea
    property alias propA: colA.propArea
    property alias propB: colB.propArea
}
