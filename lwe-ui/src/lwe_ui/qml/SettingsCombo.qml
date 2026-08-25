import QtQuick
import QtQuick.Controls.Basic
import "."

ComboBox {
    id: cb

    property bool failed: false
    property bool compact: false
    // free numeric entry through the control itself (the value-control role). Committing is
    // the consumer's job: this only reports what was typed.
    property bool freeEntry: false
    property string entryText: ""
    signal entered(string text)

    property bool editing: false

    implicitWidth: compact ? 78 : 150
    implicitHeight: compact ? 24 : 26
    width: implicitWidth
    height: implicitHeight
    font.pixelSize: compact ? Theme.fontMeta : Theme.fontControl

    contentItem: Item {
        Label {
            anchors.fill: parent
            visible: !cb.editing
            leftPadding: cb.compact ? 8 : 10
            rightPadding: cb.indicator.width + (cb.compact ? 8 : 10)
            text: cb.displayText
            color: Theme.textPrimary
            font.pixelSize: cb.font.pixelSize
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        TextInput {
            id: entry
            anchors.fill: parent
            anchors.leftMargin: cb.compact ? 8 : 10
            anchors.rightMargin: cb.compact ? 8 : 10
            visible: cb.editing
            color: Theme.textPrimary
            font.pixelSize: cb.font.pixelSize
            verticalAlignment: Text.AlignVCenter
            selectByMouse: true
            onVisibleChanged: if (visible) { text = cb.entryText; selectAll(); forceActiveFocus(); }
            // commit on Enter or blur; Escape reverts to store truth and commits nothing
            Keys.onReturnPressed: { cb.editing = false; cb.entered(entry.text); }
            Keys.onEnterPressed: { cb.editing = false; cb.entered(entry.text); }
            Keys.onEscapePressed: cb.editing = false
            onActiveFocusChanged: if (!activeFocus && cb.editing) {
                cb.editing = false;
                cb.entered(entry.text);
            }
        }
    }

    background: Rectangle {
        radius: Theme.radiusSm
        color: Theme.surface
        border.width: cb.failed ? 1.5 : 1
        border.color: cb.failed ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.9)
                    : (cb.activeFocus ? Theme.accent : Theme.border)
    }

    indicator: IconChevron {
        direction: "down"
        size: 12
        color: Theme.textSecondary
        x: cb.width - width - (cb.compact ? 8 : 10)
        y: (cb.height - height) / 2
        // no menu -> no chevron: a value chip that edits on click must not promise a
        // dropdown it does not have
        visible: !(cb.freeEntry && cb.count === 0)
    }

    readonly property int menuZone: cb.freeEntry && cb.count > 0 ? indicator.width + (compact ? 16 : 20) : 0

    MouseArea {
        // body = everything left of the chevron zone; edits directly on free-entry combos
        visible: cb.freeEntry && cb.count > 0 && !cb.editing
        anchors.fill: parent
        anchors.rightMargin: cb.menuZone
        cursorShape: Qt.IBeamCursor
        onPressed: function(mouse) { cb.editing = true; mouse.accepted = true; }
    }
    HoverHandler {
        enabled: !cb.editing && !(cb.freeEntry && cb.count > 0)
        cursorShape: cb.freeEntry && cb.count === 0 ? Qt.IBeamCursor : Qt.PointingHandCursor
    }
    HoverHandler {
        // the chevron zone of a dual-affordance combo keeps the menu promise
        enabled: !cb.editing && cb.freeEntry && cb.count > 0
        cursorShape: Qt.PointingHandCursor
        margin: 0
        parent: cb.indicator
    }

    delegate: ItemDelegate {
        id: cbDelegate
        required property var modelData
        required property int index
        // ItemDelegate carries its own intrinsic padding, which stacked on the label's
        // leftPadding below - double-padded entries that overflowed the menu (S-12.4).
        // The label's padding is the ONLY padding.
        padding: 0
        width: cb.width - 2      // inside the popup's 1px padding, not clipped by it
        height: 26
        contentItem: Label {
            text: (cb.textRole && typeof cbDelegate.modelData === "object")
                  ? String(cbDelegate.modelData[cb.textRole])
                  : String(cbDelegate.modelData)
            color: Theme.textPrimary
            font.pixelSize: cb.font.pixelSize
            verticalAlignment: Text.AlignVCenter
            leftPadding: cb.compact ? 8 : 10
        }
        background: Rectangle {
            color: cb.highlightedIndex === cbDelegate.index ? Theme.hoverWash : "transparent"
        }
    }

    popup: Popup {
        y: cb.height
        width: cb.width
        implicitHeight: Math.min(contentItem.implicitHeight, 260)
        padding: 1
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        // A free-entry chip with NO menu entries (the Speed/Volume/dial value chips carry
        // model: []) must go STRAIGHT to editing on click - an empty dropdown holding one
        // "Type a value" row is a two-click detour through a pointless menu. The menu path
        // stays for combos that actually have entries (FPS).
        onAboutToShow: if (cb.freeEntry && cb.count === 0) {
            cb.editing = true;
            close();
        }
        contentItem: ListView {
            clip: true
            implicitHeight: Math.min(contentHeight, 200)
            model: cb.popup.visible ? cb.delegateModel : null
            ScrollBar.vertical: ScrollBar {}
        }
        background: Rectangle {
            radius: Theme.radiusSm
            color: Theme.surfaceVariant
            border.width: 1
            border.color: Theme.borderStrong
        }
    }
}
