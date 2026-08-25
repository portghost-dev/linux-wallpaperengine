import QtQuick
import QtQuick.Controls.Basic
import "."

Popup {
    id: card

    property string prompt: ""
    property string verb: ""
    property bool danger: false
    property Item anchorItem: null

    signal confirmed()

    function open(item) {
        if (item !== undefined && item !== null)
            card.anchorItem = item;
        card.visible = true;
    }

    // Anchored above the control that opened it; Popup.margins clamps the card inside the
    // window, so a row near the top or the right edge never pushes it off-screen.
    parent: anchorItem !== null ? anchorItem : undefined
    y: -height - 6
    margins: 8

    modal: false
    dim: false
    // a non-modal popup takes no key focus by default, and without focus CloseOnEscape never
    // sees the key
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: 0

    background: Rectangle {
        color: Theme.surface
        radius: 8
        border.width: 1
        border.color: Theme.borderStrong
    }

    contentItem: Row {
        spacing: Theme.spacingMd
        leftPadding: 16
        rightPadding: 16
        topPadding: 14
        bottomPadding: 14

        Label {
            anchors.verticalCenter: parent.verticalCenter
            text: card.prompt
            color: Theme.textPrimary
            font.pixelSize: Theme.fontControl
        }
        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            height: 24
            width: verbLabel.implicitWidth + 18
            radius: 5
            color: verbHover.hovered
                   ? (card.danger ? Theme.dangerWash : Theme.surfaceVariant)
                   : "transparent"
            border.width: 1
            border.color: card.danger ? Theme.danger : Theme.borderStrong
            Label {
                id: verbLabel
                anchors.centerIn: parent
                text: card.verb
                color: card.danger ? Theme.danger : Theme.textPrimary
                font.pixelSize: Theme.fontMeta
            }
            HoverHandler { id: verbHover; cursorShape: Qt.PointingHandCursor }
            TapHandler {
                onTapped: {
                    card.confirmed();
                    card.close();
                }
            }
        }
    }
}
