import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import "."

Rectangle {
    id: card

    property string wpId: ""
    property string title: ""
    property url thumb: ""
    property bool inPlaylist: false
    property bool favorite: false
    property string wpType: ""
    property bool missing: false
    property bool nowPlaying: false
    // on disk but never classified good/bad (the Review scope population). The gear is
    // hidden on these: the editor's Save writes a wp override conf, which would bypass the
    // curation gate for an item that has not been classified yet.
    property bool pendingReview: false
    property real thumbHeight: 136
    readonly property int titleRowHeight: 34

    signal playlistToggled(string id, bool on)
    signal favoriteToggled(string id)
    signal gearClicked(string id)
    signal playClicked(string id)
    signal trashRequested(string id, string title)

    implicitWidth: 224
    implicitHeight: thumbHeight + titleRowHeight + 2
    radius: Theme.radiusLg
    color: Theme.surface
    clip: true

    HoverHandler { id: hover }

    Item {
        id: thumbBox
        width: parent.width
        height: card.thumbHeight

        Rectangle {
            anchors.fill: parent
            color: Theme.surfaceVariant
            topLeftRadius: Theme.radiusLg
            topRightRadius: Theme.radiusLg
        }
        Image {
            id: rawThumb
            anchors.fill: parent
            source: card.thumb
            fillMode: Image.PreserveAspectCrop
            sourceSize.width: Theme.previewCap
            asynchronous: true
            cache: true
            visible: false
            layer.enabled: true
        }
        Item {
            id: thumbMask
            anchors.fill: parent
            visible: false
            layer.enabled: true
            // corner-bleed law: never ask a sampled mask texture to pixel-agree with the
            // vector border. Inset the mask 1px on the three card-edge sides and round it to
            // (border radius - 1) so the antialiased mask seam falls UNDER the 1px border
            // overlay (drawn on top) instead of outside it. Bottom stays flush (it meets the
            // title row, an interior edge, not the card border).
            Rectangle {
                anchors.fill: parent
                anchors.topMargin: 1
                anchors.leftMargin: 1
                anchors.rightMargin: 1
                color: "white"
                topLeftRadius: Theme.radiusLg - 1
                topRightRadius: Theme.radiusLg - 1
            }
        }
        MultiEffect {
            anchors.fill: parent
            source: rawThumb
            maskEnabled: true
            maskSource: thumbMask
            visible: card.thumb.toString() !== "" && rawThumb.status === Image.Ready
        }
        Label {
            anchors.centerIn: parent
            visible: card.thumb.toString() === ""
            text: card.missing ? "files missing" : (card.wpType !== "" ? card.wpType : "no preview")
            color: card.missing ? Theme.danger : Theme.textTertiary
            font.pixelSize: Theme.fontMeta
        }

        Rectangle {
            anchors.fill: parent
            topLeftRadius: Theme.radiusLg
            topRightRadius: Theme.radiusLg
            color: Theme.scrimHover
            opacity: hover.hovered ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 150 } }
        }

        Item {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.margins: Theme.spacingSm
            width: 20; height: 20
            visible: card.inPlaylist || hover.hovered
            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSm
                color: card.inPlaylist ? Theme.accent : Theme.scrimPlate
                border.width: card.inPlaylist ? 0 : 1
                border.color: Theme.checkHoverBorder
            }
            Item {
                anchors.centerIn: parent
                width: 10; height: 8
                visible: card.inPlaylist
                Rectangle { x: 0; y: 4; width: 5; height: 2; radius: 1; rotation: 45; color: Theme.onAccent }
                Rectangle { x: 3; y: 3; width: 8; height: 2; radius: 1; rotation: -50; color: Theme.onAccent }
            }
            TapHandler { onTapped: card.playlistToggled(card.wpId, !card.inPlaylist) }
        }

        Column {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme.spacingSm
            spacing: Theme.spacingXs

            Rectangle {
                width: 26; height: 26; radius: Theme.radiusSm
                color: Theme.scrimPlate
                visible: card.favorite || hover.hovered
                IconStar {
                    anchors.centerIn: parent
                    size: 14
                    filled: card.favorite
                    color: card.favorite ? Theme.accent : "#F2F2F2"
                }
                TapHandler { onTapped: card.favoriteToggled(card.wpId) }
            }
            Rectangle {
                width: 26; height: 26; radius: Theme.radiusSm
                color: Theme.scrimPlate
                opacity: hover.hovered ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 150 } }
                IconGear {
                    anchors.centerIn: parent
                    size: 15
                    color: "#A0A0A0"
                }
                TapHandler { onTapped: card.gearClicked(card.wpId) }
            }
        }

        Rectangle {
            anchors.centerIn: parent
            width: 44; height: 44; radius: 22
            color: Theme.accent
            opacity: hover.hovered && !card.missing ? 1 : 0
            visible: opacity > 0
            scale: hover.hovered ? 1 : 0.9
            Behavior on opacity { NumberAnimation { duration: 150 } }
            Behavior on scale { NumberAnimation { duration: 150 } }
            Canvas {
                // Canvas paints ONCE and reads its Theme color AT PAINT TIME, so a theme
                // switch changed the binding but left the pixels alone (glyphs kept the old
                // palette until an app restart). Theme.rev ticks on every theme change.
                property int themeRev: Theme.rev
                onThemeRevChanged: requestPaint()
                anchors.centerIn: parent
                width: 16; height: 16
                onPaint: {
                    var c = getContext("2d");
                    c.reset();
                    c.fillStyle = Theme.onAccent;
                    c.beginPath();
                    c.moveTo(3, 1); c.lineTo(14, 8); c.lineTo(3, 15); c.closePath();
                    c.fill();
                }
            }
            TapHandler { onTapped: card.playClicked(card.wpId) }
        }

        Rectangle {
            anchors.left: parent.left
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spacingSm
            visible: card.wpType !== ""
            radius: Theme.radiusXs
            color: Theme.scrimPlate
            width: badge.implicitWidth + 14
            height: badge.implicitHeight + Theme.spacingXs
            Label {
                id: badge
                anchors.centerIn: parent
                text: card.wpType
                // Design amendment: plate = legibility (immutable scrim), text =
                // identity - #D4D4D4 tinted 18% toward the theme accent, derived in
                // the store so contrast holds by construction
                color: Theme.badgeText
                font.pixelSize: Theme.fontMeta
            }
        }

        Rectangle {
            id: trashBtn
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spacingSm
            width: 24
            height: 24
            radius: Theme.radiusSm
            color: Theme.scrimPlate
            opacity: hover.hovered ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 150 } }
            IconTrash {
                anchors.centerIn: parent
                size: 15
                color: Theme.danger
            }
            TapHandler { onTapped: card.trashRequested(card.wpId, card.title) }
        }
    }

    Item {
        anchors.top: thumbBox.bottom
        width: parent.width
        height: 34

        Row {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: Theme.spacingMd
            anchors.rightMargin: Theme.spacingMd
            spacing: Theme.spacingSm

            Rectangle {
                width: 6; height: 6; radius: 3
                anchors.verticalCenter: parent.verticalCenter
                color: Theme.accent
                visible: card.nowPlaying
            }
            Label {
                id: titleLabel
                width: parent.width - (card.nowPlaying ? 6 + Theme.spacingSm : 0)
                text: card.title
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBody13
                elide: Text.ElideRight
                maximumLineCount: 1
                HoverHandler { id: titleHover }
                ToolTip.visible: titleHover.hovered && titleLabel.truncated
                ToolTip.delay: 400
                ToolTip.text: card.title
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: card.missing ? Theme.dangerWash : "transparent"
        radius: card.radius
        border.width: 1
        border.color: card.missing ? Theme.danger
                      : (hover.hovered ? Theme.borderStrong : Theme.border)
    }
}
