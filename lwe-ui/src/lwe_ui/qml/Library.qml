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

    // drag state: the lifted card follows the pointer, the origin slot stays
    // open, the slot under the pointer is marked, the model holds the provisional order
    property string dragId: ""
    property var dragSource: null
    // the lifted card's face, copied at lift time, so the ghost never depends on the delegate
    property string dragTitle: ""
    property url dragThumb: ""
    property bool dragInPlaylist: false
    property bool dragFavorite: false
    property string dragType: ""
    property bool dragMissing: false
    property int liftedRow: -1     // the lifted card's own open slot in the provisional order
    property int hoverRow: -1
    property real pointerX: 0
    property real pointerY: 0
    // the edge band scrolls only once the pointer has left it since the lift: a card picked up
    // in the bottom row starts inside the band, and the first frames would nudge the view
    property bool bandArmed: false

    function startDrag(card) {
        if (!backend.beginDrag(card.wpId))
            return;
        bandArmed = false;
        dragSource = card;
        dragTitle = card.title;
        dragThumb = card.thumb;
        dragInPlaylist = card.inPlaylist;
        dragFavorite = card.favorite;
        dragType = card.wpType;
        dragMissing = card.missing;
        dragId = card.wpId;
        liftedRow = backend.orderModel.rowOf(card.wpId);
        hoverRow = liftedRow;
    }
    // the grid row under a point in the grid's own coordinates, -1 outside, -2 in the band
    function rowAt(px, py) {
        if (px < 0 || px >= grid.width || py < 0 || py >= grid.height)
            return -1;
        var cx = px + grid.contentX, cy = py + grid.contentY;
        var col = Math.min(grid.cols - 1, Math.floor(cx / grid.cellWidth));
        var band = grid.bandTop();
        if (band >= 0 && cy >= band && cy < band + grid.gap + grid.poolOffset + 1)
            return -2;
        var laidY = band >= 0 && cy >= band ? cy - grid.poolOffset : cy;
        var r = Math.floor((laidY - grid.originY) / grid.cellHeight);
        var row = Math.max(0, r) * grid.cols + col;
        return row >= grid.count ? grid.count : row;
    }
    function moveDrag(sceneX, sceneY) {
        var p = root.mapFromItem(null, sceneX, sceneY);
        pointerX = p.x;
        pointerY = p.y;
        updateHover();
    }
    function updateHover() {
        if (dragId === "")
            return;
        var p = grid.mapFromItem(root, pointerX, pointerY);
        var row = rowAt(p.x, p.y);
        if (row !== hoverRow) {
            hoverRow = row;
            backend.dragOver(row);
            liftedRow = backend.orderModel.rowOf(dragId);
        }
    }
    Connections {
        target: backend.orderModel
        function onModelAboutToBeReset() { if (root.dragId !== "") root.cancelDrag(); }
    }
    function cancelDrag() {
        hoverRow = -1;
        backend.dragOver(-1);
        endDrag();
    }
    function endDrag() {
        if (dragId === "")
            return;
        // clear the lifted state first: whatever the drop does, the grid must never stay armed
        var source = dragSource;
        dragId = "";
        dragSource = null;
        if (source && source.lifting)
            source.cancelLift();
        liftedRow = -1;
        hoverRow = -1;
        try {
            backend.endDrag(true);
        } catch (e) {
            console.warn("drop failed:", e);
        }
        grid.returnToBounds();
        grid.snapToRow();
    }

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

        readonly property int gap: Theme.compact ? Theme.gridGapCompact : Theme.spacingLg
        // the hairline band between the blocks: one gap above the line and one below,
        // so the pool rows sit poolOffset lower than the grid lays them
        readonly property int poolOffset: backend.orderModel.hairlineIndex >= 0 ? gap + 1 : 0
        readonly property int memberRows: Math.ceil((backend.orderModel.memberCount + backend.orderModel.fillerCount) / cols)
        // content y of the last member card's bottom edge, from the view's own layout; -1 without a band
        function bandTop() {
            if (backend.orderModel.hairlineIndex < 0)
                return -1;
            return originY + memberRows * cellHeight - gap;
        }
        // scroll so a row top sits at the top of the view: whole rows show after a drop
        // moved the view. A pool row's top sits poolOffset below its laid position.
        function snapToRow() {
            var band = bandTop();
            var shift = band >= 0 && contentY > band ? poolOffset : 0;
            var r = Math.round((contentY - originY - shift) / cellHeight);
            var target = originY + Math.max(0, r) * cellHeight + shift;
            contentY = Math.max(originY, Math.min(target, originY + contentHeight - height));
        }
        // columns: the count whose tiles land nearest the target width, never a floor
        // target tile: 176 compact; flagship ramps 216 at a 1280 window to 260 at 2560
        readonly property int targetTile: Theme.compact ? 176
                                        : Math.round(Math.max(216, Math.min(260, 216 + (width - 1200) * 44 / 1280)))
        readonly property int maxTile: 320
        readonly property int cols: Math.max(1, Math.round(width / (targetTile + gap)))
        // a column change is a relayout: re-cut padding lands with the transitions off and the
        // layout forced in one turn; a view never re-places an item mid-transition, so inside
        // the reflow window of any row edit the change rebuilds the rows instead
        property bool recutting: false
        property real lastRowEdit: 0
        Connections {
            target: backend.orderModel
            function onRowsRemoved() { grid.lastRowEdit = Date.now() }
            function onRowsInserted() { grid.lastRowEdit = Date.now() }
            function onRowsMoved() { grid.lastRowEdit = Date.now() }
        }
        function setCols() {
            if (root.dragId === "" && Date.now() - lastRowEdit < Motion.removeReflow + 40) {
                backend.orderModel.resetColumns(cols);
                return;
            }
            recutting = true;
            try {
                backend.orderModel.setColumns(cols);
                forceLayout();
            } finally {
                recutting = false;
            }
        }
        onColsChanged: setCols()
        Component.onCompleted: setCols()
        readonly property int tileW: Math.min(maxTile, cellWidth - gap)
        readonly property real baseThumbH: tileW * 10 / 16
        readonly property int nominalCellH: Math.round(baseThumbH) + 34 + gap

        // OPTICAL ROW FITTING (v1.6-a2, BIDIRECTIONAL). Only the thumb height flexes (the
        // title row and gaps never move); the flex budget is +/-10% of the 16:10 base.
        // The hairline band between the blocks is paid for first: the rows fit the height
        // that is left, so the rows below the band land flush too
        readonly property real fitHeight: height - poolOffset
        readonly property int rowsFit: Math.max(1, Math.floor(fitHeight / nominalCellH))
        // 3a-shrink: if fitting one MORE row overshoots by <= the budget, compress all
        // rows equally so N+1 land flush (the crop absorbs it) - this is the fix for the
        // "almost-fits" clip where the naive floor drops the last row to a sliver.
        readonly property real shrinkOvershoot: (rowsFit + 1) * nominalCellH - fitHeight
        readonly property real shrinkPerRow: shrinkOvershoot / (rowsFit + 1)
        // the band's share rides on top of the optical budget: rows that fit without the band
        // still fit with it, a few pixels shorter, rather than dropping to a peeking row
        readonly property bool canShrink: shrinkOvershoot > 0
                                          && shrinkPerRow <= baseThumbH * 0.10 + poolOffset / (rowsFit + 1)
        // 3a-grow: else absorb a small leftover so the rows that DO fit land flush
        readonly property real growLeftover: fitHeight - rowsFit * nominalCellH
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
        model: backend.orderModel
        // row removals do not re-lay the view: a viewport left below the content returns by hand
        onContentHeightChanged: if (root.dragId === "" && contentY > Math.max(0, contentHeight - height)) returnToBounds()
        // every delegate stays alive while a card is lifted: the drag's handler lives on the
        // lifted delegate, and the view would otherwise recycle it as the grid scrolls
        cacheBuffer: root.dragId !== "" ? Math.max(contentHeight, cellHeight * 4) : cellHeight * 4
        interactive: root.dragId === ""   // no flicking under a lifted card

        // grid-removal contract (v2.3.1): the trashed card fades, the rest reflow to close the
        // gap. Shared timings from Motion so every grid removes the same way.
        remove: Transition {
            enabled: !grid.recutting
            NumberAnimation { property: "opacity"; to: 0; duration: Motion.removeFade }
        }
        displaced: Transition {
            enabled: !grid.recutting
            NumberAnimation { properties: "x,y"; duration: Motion.removeReflow; easing.type: Motion.removeReflowEasing }
        }
        move: Transition {
            enabled: !grid.recutting
            NumberAnimation { properties: "x,y"; duration: Motion.removeReflow; easing.type: Motion.removeReflowEasing }
        }

        // one hairline between the blocks with a gap above and below; nothing when either is empty
        Rectangle {
            parent: grid.contentItem   // scrolls with the cells; a plain child of the view would not
            z: 1
            visible: backend.orderModel.hairlineIndex >= 0
            x: 0
            y: grid.bandTop() + grid.gap
            width: grid.width - grid.gap
            height: 1
            color: Theme.hairline
        }
        footer: Item { height: grid.poolOffset }
        // the open slot of the lifted card: accent while the pointer is over the grid (that is
        // where the card lands), faint once the pointer leaves (the card would drop home)
        SlotOutline {
            parent: grid.contentItem
            z: 2
            visible: root.dragId !== "" && root.liftedRow >= 0
            row: root.liftedRow
            // accent while the card would land as a member; faint while it would drop home in the pool
            color: root.liftedRow < backend.orderModel.memberCount ? Theme.accent : Theme.hairline
        }

        delegate: WallpaperCard {
            required property var model
            required property int index

            visible: !model.filler
            // pool rows sit poolOffset below their laid position (the hairline band)
            readonly property real poolShift: index >= backend.orderModel.hairlineIndex && backend.orderModel.hairlineIndex >= 0 ? grid.poolOffset : 0
            transform: Translate { y: poolShift }
            lifted: root.dragId !== "" && model.id === root.dragId
            onDragStarted: function(card) { root.startDrag(card); }
            onDragMoved: function(sceneX, sceneY) { root.moveDrag(sceneX, sceneY); }
            onDragEnded: root.endDrag()

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
            refused: model.refused
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

    // a drag continues anywhere on screen while the button is held; the release arrives from
    // wherever it happens. This only catches a handler left armed after a release we never saw.
    HoverHandler {
        id: windowHover
        onPointChanged: if (root.dragId !== "" && !(point.pressedButtons & Qt.LeftButton) && root.dragSource && !root.dragSource.lifting) root.endDrag()
    }

    // the lifted card: scale 0.75, top-left at pointer + (12, 12), face identical to rest
    WallpaperCard {
        id: liftGhost
        visible: root.dragId !== ""
        x: root.pointerX + 12
        y: root.pointerY + 12
        z: 10
        width: grid.tileW
        height: grid.cellHeight - grid.gap
        thumbHeight: Math.floor(grid.thumbH)
        scale: 0.75
        transformOrigin: Item.TopLeft
        enabled: false
        wpId: root.dragId
        title: root.dragTitle
        thumb: root.dragThumb
        inPlaylist: root.dragInPlaylist
        favorite: root.dragFavorite
        wpType: root.dragType
        missing: root.dragMissing
    }

    // auto-scroll within 40 px of the grid's top or bottom edge, up to 12 px per frame
    Timer {
        interval: 16
        repeat: true
        running: root.dragId !== ""
        onTriggered: {
            // the handler released without telling us (pointer left the window, delegate
            // recycled): end the drag rather than scroll forever
            if (!root.dragSource || !root.dragSource.lifting) {
                root.endDrag();
                return;
            }
            // within 40 px of an edge the pace grows with depth; past the edge it is full pace,
            // so a large move sweeps the library
            var band = 40, maxStep = 12;
            var p = grid.mapFromItem(root, root.pointerX, root.pointerY);
            var inBand = p.y < band || p.y > grid.height - band;
            if (!root.bandArmed) {
                if (!inBand)
                    root.bandArmed = true;
                root.updateHover();
                return;
            }
            // the view moves its origin when rows shift above the viewport; the content lies
            // in [originY, originY + contentHeight], not from zero
            var minY = grid.originY;
            var maxY = Math.max(minY, grid.originY + grid.contentHeight - grid.height);
            if (p.y < band && grid.contentY > minY)
                grid.contentY = Math.max(minY, grid.contentY - maxStep * Math.min(1, (band - p.y) / band));
            else if (p.y > grid.height - band && grid.contentY < maxY)
                grid.contentY = Math.min(maxY, grid.contentY + maxStep * Math.min(1, (p.y - (grid.height - band)) / band));
            root.updateHover();
        }
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
