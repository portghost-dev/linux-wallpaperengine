import QtQuick
import QtQuick.Controls.Basic
import "."

ComboBox {
    id: cb

    // sec 3 shrink floor: a narrowed session bar must not squeeze a combo to nothing.
    // 0 = no floor, so every existing consumer is unaffected.
    property int minWidth: 0
    implicitHeight: 26
    font.pixelSize: Theme.fontControl
    contentItem: Label {
        leftPadding: Theme.spacingSm
        rightPadding: cb.indicator.width + Theme.spacingSm
        text: cb.displayText
        color: Theme.textPrimary
        font.pixelSize: Theme.fontControl
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: Theme.radiusSm
        color: Theme.surface
        border.width: 1
        border.color: cb.activeFocus ? Theme.accent : Theme.border
    }
    // compact 12px chevron: the stock indicator plus its padding ate ~30px of a narrow
    // combo's text room, which is what left values rendering as truncated stubs
    indicator: IconChevron {
        direction: "down"
        size: 12
        color: Theme.textTertiary
        x: cb.width - width - 8
        y: (cb.height - height) / 2
    }
    delegate: ItemDelegate {
        id: cbDelegate
        required property var modelData
        required property int index
        width: cb.width
        contentItem: Label {
            // map models (textRole set) hand the delegate the whole map; a raw modelData
            // assignment then spams "Unable to assign QVariantMap to QString" per row
            text: (cb.textRole && typeof cbDelegate.modelData === "object")
                  ? String(cbDelegate.modelData[cb.textRole])
                  : String(cbDelegate.modelData)
            color: Theme.textPrimary
            font.pixelSize: Theme.fontControl
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            color: cb.highlightedIndex === cbDelegate.index ? Theme.hoverWash : "transparent"
        }
    }
    popup: Popup {
        y: cb.height
        width: cb.width
        implicitHeight: Math.min(contentItem.implicitHeight, 240)
        padding: 1
        // the stock ComboBox popup exempts the control itself from press-outside closing so
        // the control's own toggle can close it; a replacement popup must restate that or a
        // click on the combo closes-on-press then reopens-on-release (can never click closed)
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
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
