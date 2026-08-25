import QtQuick
import QtQuick.Controls.Basic
import "."

Popup {
    id: wizard
    property string wid: ""
    property string wpTitle: ""
    property bool hasCopy: false
    property int beat: 1
    property string leavesNoun: "Workshop"
    property bool hasSteamPage: false
    property int dependents: 0

    function openFor(id, title) {
        wid = id;
        wpTitle = title;
        hasCopy = workshop.trashConsequence(id).hasCopy === true;
        hasSteamPage = workshop.isSteamSubscribed(id);
        dependents = workshop.dependentCount(id);
        beat = 1;
        open();
    }
    // fires once the item is actually trashed - surfaces that display the item
    // (the editor) close on it; tile surfaces just let the model refresh
    signal trashed(string trashedWid)

    function confirmTrash() {
        workshop.trashItem(wid, "");
        wizard.trashed(wid);
        if (wizard.hasSteamPage)
            beat = 2;
        else
            wizard.close();
    }
    function openUnsubscribeOnly(id, title) {
        wid = id;
        wpTitle = title;
        hasSteamPage = workshop.isSteamSubscribed(id);
        if (!hasSteamPage)
            return;
        beat = 2;
        open();
    }

    anchors.centerIn: parent
    // sec 3 modal guard: every face is already <=620 and none was width-responsive, so
    // below a 660 window they clamp to width-24 (12px side margins) instead of
    // overhanging. Theme.usableWidth excludes the rail; modals center on the WINDOW,
    // so the rail's 64px is added back before comparing.
    width: Math.min(380, Theme.usableWidth + 64 - 24)
    height: Math.max(beat1.implicitHeight, beat2.implicitHeight)
    modal: true
    focus: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle {
        radius: Theme.radiusLg
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderStrong
        clip: true
    }
    Overlay.modal: Rectangle { color: Theme.scrimHover }

    contentItem: Item {
        ModalFace {
            id: beat1
            width: parent.width
            opacity: wizard.beat === 1 ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: Motion.fade } }
            title: "Trash \"" + wizard.wpTitle + "\"?"
            body: (wizard.dependents > 0
                   ? ("Heads up: " + wizard.dependents + " other item"
                      + (wizard.dependents === 1 ? "" : "s") + " you have use this as a base and "
                      + "will stop working without it. ")
                   : "")
                  + "It leaves " + wizard.leavesNoun + " and stays out. To re-add it later, "
                  + "clear its tombstone."
            primaryText: "Trash it"
            primaryColor: Theme.danger
            secondaryText: "Cancel"
            footerText: "Tombstones live in Settings > Library"
            onPrimaryClicked: wizard.confirmTrash()
            onSecondaryClicked: wizard.close()
            onCloseClicked: wizard.close()
            middleContent: Well {
                anchors.left: parent.left
                anchors.right: parent.right
                mode: "status"
                showDot: false
                text: wizard.hasCopy ? "Copy mode. Our copy is deleted from disk."
                                     : "Reference mode. Nothing is deleted from disk."
            }
        }

        ModalFace {
            id: beat2
            width: parent.width
            opacity: wizard.beat === 2 ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: Motion.fade } }
            title: "Trashed. One step to free disk space"
            body: "Steam keeps subscribed items downloaded. Unsubscribe on the item's Workshop "
                  + "page and Steam removes it from your computer."
            primaryText: "Unsubscribe on Steam"
            primaryColor: Theme.accent
            secondaryText: "Keep it downloaded"
            onPrimaryClicked: { workshop.openItemPage(wizard.wid); wizard.close(); }
            onSecondaryClicked: wizard.close()
            onCloseClicked: wizard.close()
            middleContent: Well {
                anchors.left: parent.left
                anchors.right: parent.right
                mode: "value"
                text: (wizard.visible && wizard.beat === 2) ? workshop.itemLinkText(wizard.wid) : ""
                onCopyClicked: workshop.copyItemLink(wizard.wid)
            }
        }
    }
}
