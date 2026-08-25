pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import "."

Item {
    id: root

    signal openEditor(string id)
    signal openSettings()

    property string scope: "all"
    property string searchQuery: ""
    property string nowPlayingId: ""

    // setScope lives on the FILTER MODEL, not Backend (calling backend.setScope threw
    // "not a function" on every rail click, killing Favorites and Review)
    onScopeChanged: backend.filterModel.setScope(scope)

    GridView {
        id: grid
        objectName: "libraryGrid"
        anchors.fill: parent
        anchors.margins: Theme.spacingLg
        anchors.rightMargin: 0   // the last column's trailing cell gap is the right padding
        clip: true

        readonly property int gap: Theme.spacingLg
        readonly property int minTile: 216
        readonly property int maxTile: 320
        // auto-fit: GridView cells are uniform and each reserves tile+gap (the last cell's
        // trailing gap is the right padding, mirroring the left margin), so the most
        // columns that keep tiles >= minTile is floor(width / (minTile + gap)). cellWidth =
        // width/cols then makes GridView lay exactly that many columns filling the width.
        readonly property int cols: Math.max(1, Math.floor(width / (minTile + gap)))
        readonly property int tileW: Math.min(maxTile, cellWidth - gap)
        readonly property real baseThumbH: tileW * 10 / 16
        readonly property int nominalCellH: Math.round(baseThumbH) + 34 + gap

        // OPTICAL ROW FITTING (v1.6-a2, BIDIRECTIONAL). Only the thumb height flexes (the
        // title row and gaps never move); the flex budget is +/-10% of the 16:10 base.
        readonly property int rowsFit: Math.max(1, Math.floor(height / nominalCellH))
        // 3a-shrink: if fitting one MORE row overshoots by <= the budget, compress all
        // rows equally so N+1 land flush (the crop absorbs it) - this is the fix for the
        // "almost-fits" clip where the naive floor drops the last row to a sliver.
        readonly property real shrinkOvershoot: (rowsFit + 1) * nominalCellH - height
        readonly property real shrinkPerRow: shrinkOvershoot / (rowsFit + 1)
        readonly property bool canShrink: shrinkOvershoot > 0
                                          && shrinkPerRow <= baseThumbH * 0.10
        // 3a-grow: else absorb a small leftover so the rows that DO fit land flush
        readonly property real growLeftover: height - rowsFit * nominalCellH
        readonly property real growPerRow: growLeftover / rowsFit
        readonly property bool canGrow: growLeftover > 0 && growLeftover < 24
                                        && growPerRow <= baseThumbH * 0.10
        // 3b: neither in-band -> leave nominal; a >=24px residual peeks the next row
        readonly property int rowsVisible: canShrink ? rowsFit + 1 : rowsFit
        readonly property real thumbH: canShrink ? baseThumbH - shrinkPerRow
                                     : canGrow   ? baseThumbH + growPerRow
                                     :             baseThumbH

        cellWidth: Math.floor(width / cols)
        // FLOOR the flexed thumb so rowsVisible cells never overshoot the viewport by a
        // rounding pixel (which would clip the last row's bottom border)
        cellHeight: Math.floor(thumbH) + 34 + gap
        model: backend.filterModel
        cacheBuffer: cellHeight * 4

        // grid-removal contract (v2.3.1): the trashed card fades, the rest reflow to close the
        // gap. Shared timings from Motion so every grid removes the same way.
        remove: Transition {
            NumberAnimation { property: "opacity"; to: 0; duration: Motion.removeFade }
        }
        displaced: Transition {
            NumberAnimation { properties: "x,y"; duration: Motion.removeReflow; easing.type: Motion.removeReflowEasing }
        }

        delegate: WallpaperCard {
            required property var model

            width: grid.tileW
            height: grid.cellHeight - grid.gap
            thumbHeight: Math.floor(grid.thumbH)

            wpId: model.id
            title: model.title
            thumb: model.thumb
            inPlaylist: model.inPlaylist
            favorite: model.favorite
            wpType: model.type
            missing: model.missing
            pendingReview: model.pendingReview
            nowPlaying: model.id === root.nowPlayingId

            onPlaylistToggled: function(cardId, on) { backend.setPlaylist(cardId, on); }
            onFavoriteToggled: function(cardId) { backend.toggleFavorite(cardId); }
            onGearClicked: function(cardId) { root.openEditor(cardId); }
            onPlayClicked: function(cardId) { backend.showNow(cardId); }
            onTrashRequested: function(cardId, cardTitle) {
                libraryTrashWizard.openFor(cardId, cardTitle);
            }
        }

        ScrollBar.vertical: ScrollBar {}
    }

    TrashWizard {
        id: libraryTrashWizard
        objectName: "libraryTrashWizard"
        parent: Overlay.overlay
        leavesNoun: "the library"
    }

    Column {
        anchors.centerIn: parent
        spacing: Theme.spacingMd
        visible: grid.count === 0

        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            text: {
                if (backend.totalCount() === 0)
                    return "No wallpapers yet. Point Settings > Library at your Steam workshop folder.";
                // the search/filter no-match echoes the query. With no query it is a
                // filter-only exclusion, so fall back to the generic line rather than 'matches ""'.
                if (root.searchQuery !== "")
                    return "Nothing matches \"" + root.searchQuery + "\".";
                return "Nothing matches the current filters.";
            }
            color: Theme.textSecondary
            font.pixelSize: Theme.fontBody13
        }
        Button {
            id: openSettingsBtn
            anchors.horizontalCenter: parent.horizontalCenter
            visible: backend.totalCount() === 0
            text: "Open settings"
            onClicked: root.openSettings()
            contentItem: Label {
                text: openSettingsBtn.text
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBody13
                horizontalAlignment: Text.AlignHCenter
            }
            background: Rectangle {
                radius: Theme.radiusSm
                color: openSettingsBtn.hovered ? Theme.hoverWash : "transparent"
                border.width: 1
                border.color: Theme.border
            }
        }
    }
}
