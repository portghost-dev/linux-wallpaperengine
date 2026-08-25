import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import "."

Popup {
    id: modal
    objectName: "manualAddModal"

    anchors.centerIn: parent
    // sec 3 modal guard: every face is already <=620 and none was width-responsive, so
    // below a 660 window they clamp to width-24 (12px side margins) instead of
    // overhanging. Theme.usableWidth excludes the rail; modals center on the WINDOW,
    // so the rail's 64px is added back before comparing.
    width: Math.min(520, Theme.usableWidth + 64 - 24)
    modal: true
    focus: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle {
        radius: Theme.radiusMd
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderStrong
    }
    Overlay.modal: Rectangle { color: Theme.scrimHover }

    // the wid actually created, so the caller can rescan and report
    signal added(string wid)

    property string chosenPath: ""
    property string errorText: ""

    function openModal() {
        chosenPath = "";
        errorText = "";
        pathField.text = "";
        open();
    }

    contentItem: ModalFace {
    id: face
    title: "Add from folder"
    body: "Point this at a wallpaper's FOLDER, not a single file. The directory containing its "
          + "project.json. It is copied in and joins Workshop for benching."
    primaryText: "Add folder"
    secondaryText: "Cancel"
    footerText: "It appears in Workshop, not the library, until you bench and add it."

    onSecondaryClicked: modal.close()
    onCloseClicked: modal.close()
    onPrimaryClicked: {
        var wid = workshop.addFromFolder(modal.chosenPath || pathField.text);
        if (wid === "") {
            modal.errorText = "That folder has no project.json, or could not be read.";
            return;
        }
        modal.added(wid);
        modal.close();
    }

    middleContent: Column {
        width: parent ? parent.width : 0
        spacing: Theme.spacingSm

        Row {
            width: parent.width
            spacing: Theme.spacingSm

            Rectangle {
                id: well
                width: parent.width - browseBtn.width - Theme.spacingSm
                height: 30
                radius: Theme.radiusSm
                color: Theme.inputWell
                border.width: 1
                border.color: modal.errorText !== "" ? Theme.danger : Theme.border
                TextInput {
                    id: pathField
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spacingSm
                    anchors.rightMargin: Theme.spacingSm
                    verticalAlignment: TextInput.AlignVCenter
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontControl
                    font.family: Theme.monoFamily
                    clip: true
                    selectByMouse: true
                    onTextChanged: { modal.chosenPath = text; modal.errorText = "" }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: pathField.text === ""
                        text: "/path/to/wallpaper-folder"
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontControl
                        font.family: Theme.monoFamily
                    }
                }
            }

            Button {
                id: browseBtn
                height: 30
                text: "Browse..."
                onClicked: folderPicker.open()
                contentItem: Label {
                    text: browseBtn.text
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontControl
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    implicitWidth: 84
                    radius: Theme.radiusSm
                    color: browseBtn.hovered ? Theme.hoverWash : "transparent"
                    border.width: 1
                    border.color: Theme.border
                }
            }
        }

        Label {
            width: parent.width
            visible: modal.errorText !== ""
            text: modal.errorText
            color: Theme.danger
            font.pixelSize: Theme.fontMeta
            wrapMode: Text.WordWrap
        }
    }

    }

    // a DIRECTORY chooser, never a file chooser - the unit of a wallpaper is its folder
    FolderDialog {
        id: folderPicker
        title: "Choose a wallpaper folder"
        onAccepted: {
            modal.chosenPath = selectedFolder.toString();
            pathField.text = modal.chosenPath;
            modal.errorText = "";
        }
    }
}
