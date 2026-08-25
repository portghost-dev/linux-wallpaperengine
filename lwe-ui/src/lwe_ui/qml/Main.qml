import QtQuick
import QtQuick.Controls.Basic
import "."

// Root window: frameless shell at 1280x700 minimum. Layout is a 64px nav rail on the
// left, then header (48) / content / deck (72). The library view fills the content area;
// settings, developer, and the editor takeover mount there. The editor is a center
// takeover (EditorView); the bench runs inside it through the bench bridge. The archived
// slide-over editor/bench drawers were removed (ledger A5) - the takeover owns the flow.
ApplicationWindow {
    id: window
    visible: true
    width: 1280
    height: 700
    // The floor drops 1080 -> 640 and the band between reflows (it does
    // NOT scroll - v1.6 rule 7's scroll half was never built and stays deferred). Note this
    // minimum is a REQUEST: hyprland does not honor it under tiling, which is why the
    // overprinted header was observable at 1080 in the first place. Lowering it makes Qt agree
    // with what the compositor already does; it does not by itself keep anything above 640.
    minimumWidth: 640
    minimumHeight: 640

    // the single responsive input, pushed into the Theme singleton (which cannot see a window).
    // Rail is 64 and never sheds, so USABLE is what every surface actually gets.
    onWidthChanged: Theme.usableWidth = Math.max(0, width - rail.width)
    Component.onCompleted: Theme.usableWidth = Math.max(0, width - rail.width)
    title: "LWE Control Panel"
    color: Theme.base
    flags: Qt.Window | Qt.FramelessWindowHint

    property var engineStatus: ({})
    Timer {
        interval: 2000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: window.engineStatus = backend.status()
    }

    Timer {
        id: statusPoke
        interval: 350
        repeat: true
        property int remaining: 0
        onTriggered: {
            window.engineStatus = backend.status();
            if (--remaining <= 0)
                stop();
        }
    }
    Connections {
        target: backend
        function onStatusChanged() { statusPoke.remaining = 4; statusPoke.restart() }
    }

    property string masterState: "absent"
    Timer {
        interval: 5000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            window.masterState = backend.masterState();
            rail.reviewCount = backend.pendingReviewCount();
        }
    }

    property string currentView: "library"

    // approve/trash/import all emit settingsChanged - the badge answers immediately
    // instead of on the 5s poll (review F13)
    Connections {
        target: backend
        function onSettingsChanged() { rail.reviewCount = backend.pendingReviewCount() }
    }

    Shortcut {
        sequence: "Escape"
        onActivated: {
            // an open popup owns this Escape (review F14): closing the trash wizard
            // must not also eject the scope (which would clear the kept lingers)
            var ov = window.Overlay.overlay;
            if (ov) {
                for (var i = 0; i < ov.children.length; i++)
                    if (ov.children[i].visible)
                        return;
            }
            if (window.currentView !== "library")
                window.currentView = "library";
        }
    }

    Rail {
        id: rail
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        // F23: the active wash + accent bar follow whichever view is actually mounted.
        // Settings/Developer takeovers activate their own rail item and de-active the
        // scope item; the editor drawer is opened from a card gear (not the rail), so it
        // leaves the last scope item active rather than activating nothing.
        activeItem: (window.currentView === "developer" || window.currentView === "settings"
                     || window.currentView === "workshop")
                    ? window.currentView : rail.currentScope
        // a scope click is also the EXIT from any takeover (the rail is the navigation;
        // without this, All/Favorites changed the filter but a takeover stayed mounted -
        // no way back to the tiles). Workshop (16a) mounts its own takeover view.
        onScopeSelected: function(scope) {
            window.currentView = (scope === "workshop") ? "workshop" : "library";
        }
        onDeveloperRequested: window.currentView = "developer"
        onSettingsRequested: window.currentView = "settings"
    }

    // header counts rev: workshop/library counts are slot reads, not NOTIFY properties, so a
    // bump on either bridge's stateChanged re-evaluates the Workshop header counter + tombstone count.
    property int hdrRev: 0
    Connections { target: workshop; function onStateChanged() { window.hdrRev++ } }
    Connections { target: backend; function onSettingsChanged() { window.hdrRev++ } }

    HeaderBar {
        id: header
        anchors.left: rail.right
        anchors.right: parent.right
        anchors.top: parent.top
        engineStatus: window.engineStatus
        // the RAW unit state, not a boolean: the header's status light has to tell "failed"
        // from "inactive" and "activating" from "active" to color its track
        masterState: window.masterState
        onMasterToggled: function(on) {
            backend.setMaster(on);
            window.masterState = backend.masterState();
        }
        workshopActive: window.currentView === "workshop"
        workshopCounts: (window.hdrRev, backend.pendingReviewCount() + " new · "
                         + backend.totalCount() + " in library")
    }

    Deck {
        id: deck
        anchors.left: rail.right
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        engineStatus: window.engineStatus
        masterActive: window.masterState === "active"
        // gear -> the editor pre-navigated to the now-playing wallpaper (sec 4.1). The deck
        // already guarded the draft cases (sec 4.2): same-wid means front-without-reopen.
        onOpenWallpaperPanel: function(wid) {
            if (editor.currentWid() !== wid)
                editor.open(wid);
            window.currentView = "editor";
        }
    }

    Library {
        id: library
        anchors.left: rail.right
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: deck.top
        visible: window.currentView === "library"
        scope: rail.currentScope
        searchQuery: header.query
        nowPlayingId: (window.engineStatus.current || "")
        onOpenSettings: window.currentView = "settings"
        onOpenEditor: function(id) {
            editor.open(id);          // load the bridge for this wid (emits loaded())
            window.currentView = "editor";
        }
    }

    WorkshopView {
        anchors.left: rail.right
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: deck.top
        visible: window.currentView === "workshop"
        onOpenEditor: function(id) {
            editor.open(id);
            window.currentView = "editor";
        }
    }

    SettingsView {
        anchors.left: rail.right
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: deck.top
        visible: window.currentView === "settings"
        onClosed: window.currentView = "library"
    }

    EditorView {
        anchors.left: rail.right
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: deck.top
        visible: window.currentView === "editor"
        engineStatus: window.engineStatus
        onClosed: window.currentView = "library"
    }

    DevView {
        anchors.left: rail.right
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: deck.top
        visible: window.currentView === "developer"
        // the same 2 s status poll the header and deck already read, so the dev area can
        // show the LIVE DAEMON instead of only whatever bench it spawned itself
        engineStatus: window.engineStatus
        onClosed: window.currentView = "library"
    }

    Rectangle {
        id: countsOnRule
        color: Theme.base
        width: countsRuleLabel.implicitWidth + 16
        height: countsRuleLabel.implicitHeight
        anchors.right: parent.right
        anchors.rightMargin: 16                 // the grid's own padding; lines up with the
                                                // last tile's right edge
        y: header.y + header.height - height / 2
        Label {
            id: countsRuleLabel
            anchors.centerIn: parent
            text: header.workshopActive ? header.workshopCounts : header.countsText
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMeta
        }
    }

    // Exhibit chips for windowed A/B: two frameless label windows the DevBridge parks on
    // each engine window via hyprctl (client-side placement is a Wayland no-op; the
    // compositor moves them, and the bridge's follower keeps them glued through drags).
    // The titles are the placement handles - the bridge finds them by title.
    property int abRev: 0
    Connections { target: dev; function onStateChanged() { window.abRev++ } }
    component ExhibitChip: Window {
        id: chip
        property string side: "A"
        title: "lwe-chip-" + side
        visible: (window.abRev, dev.abRunning())
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        width: 170
        height: 44
        color: Theme.base
        Rectangle {
            anchors.fill: parent
            color: Theme.base
            border.width: 2
            border.color: chip.side === "A" ? Theme.accent : Theme.warning
            Label {
                anchors.centerIn: parent
                text: "EXHIBIT " + chip.side
                color: Theme.textPrimary
                font.pixelSize: 17
                font.weight: Theme.weightMedium
                font.family: Theme.monoFamily
            }
        }
        ExhibitGestures { side: chip.side; cursorShape: Qt.SizeAllCursor }
    }

    // Shared gesture surface: press-drag streams deltas the bridge turns into
    // compositor-side moves (any monitor); double-click toggles maximized. The drag
    // state is cleared BEFORE the fullscreen toggle and on grab-cancel - a toggle
    // mid-press can eat the release, which left the follower freeze stuck and the
    // chip orphaned (owner finding). The bridge keeps a 2s dead-man as the backstop.
    component ExhibitGestures: MouseArea {
        id: gest
        property string side: "A"
        anchors.fill: parent
        property real accX: 0
        property real accY: 0
        property real lastX: 0
        property real lastY: 0
        function endDrag() {
            gestFlush.stop();
            accX = 0; accY = 0;
            dev.exhibitDragActive(gest.side, false);
        }
        onPressed: (mouse) => {
            lastX = mouse.x; lastY = mouse.y;
            accX = 0; accY = 0;
            dev.exhibitDragActive(gest.side, true);
            gestFlush.start();
        }
        onReleased: {
            if (accX !== 0 || accY !== 0)
                dev.exhibitDragBy(gest.side, Math.round(accX), Math.round(accY));
            endDrag();
        }
        onCanceled: endDrag()
        onPositionChanged: (mouse) => {
            if (!pressed) return;
            accX += mouse.x - lastX;
            accY += mouse.y - lastY;
            lastX = mouse.x; lastY = mouse.y;
        }
        onDoubleClicked: {
            endDrag();
            dev.exhibitToggleFullscreen(gest.side);
        }
        Timer {
            id: gestFlush
            interval: 50; repeat: true
            onTriggered: {
                if (gest.accX === 0 && gest.accY === 0) return;
                dev.exhibitDragBy(gest.side, Math.round(gest.accX), Math.round(gest.accY));
                gest.accX = 0; gest.accY = 0;
            }
        }
    }

    // Transparent full-window gesture surface glued over each exhibit: the WINDOW
    // itself takes press-drag and double-click (the original ask). The engine ignores
    // mouse input (--disable-mouse), so stealing its clicks costs nothing. The bridge
    // sizes + positions these; the chip rides above as the visible label.
    component ExhibitOverlay: Window {
        id: ovl
        property string side: "A"
        title: "lwe-overlay-" + side
        visible: (window.abRev, dev.abRunning())
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        width: 640
        height: 360
        color: "transparent"
        ExhibitGestures { side: ovl.side }
    }
    ExhibitOverlay { side: "A" }
    ExhibitOverlay { side: "B" }
    ExhibitChip { side: "A" }
    ExhibitChip { side: "B" }

    Rectangle {
        id: appNotice
        property string text: ""
        visible: text !== ""
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 14
        z: 999
        width: appNoticeLabel.implicitWidth + 28
        height: appNoticeLabel.implicitHeight + 14
        radius: Theme.radiusSm
        color: Theme.surfaceVariant
        border.width: 1
        border.color: Theme.borderStrong
        Label {
            id: appNoticeLabel
            anchors.centerIn: parent
            text: appNotice.text
            color: Theme.textPrimary
            font.pixelSize: Theme.fontMeta
        }
        Timer {
            id: appNoticeTimer
            interval: 6000
            onTriggered: appNotice.text = ""
        }
        Connections {
            target: backend
            function onNotice(msg) { appNotice.text = msg; appNoticeTimer.restart(); }
        }
    }

    // frameless window: 6px edge + 12px corner zones hand the drag to the compositor
    // (startSystemResize), which is what gives KDE/KWin floating windows their resize
    component ResizeEdge: MouseArea {
        property int edges
        z: 900
        acceptedButtons: Qt.LeftButton
        onPressed: window.startSystemResize(edges)
    }
    ResizeEdge { edges: Qt.LeftEdge; cursorShape: Qt.SizeHorCursor
                 anchors { left: parent.left; top: parent.top; bottom: parent.bottom; topMargin: 12; bottomMargin: 12 } width: 6 }
    ResizeEdge { edges: Qt.RightEdge; cursorShape: Qt.SizeHorCursor
                 anchors { right: parent.right; top: parent.top; bottom: parent.bottom; topMargin: 12; bottomMargin: 12 } width: 6 }
    ResizeEdge { edges: Qt.TopEdge; cursorShape: Qt.SizeVerCursor
                 anchors { top: parent.top; left: parent.left; right: parent.right; leftMargin: 12; rightMargin: 12 } height: 6 }
    ResizeEdge { edges: Qt.BottomEdge; cursorShape: Qt.SizeVerCursor
                 anchors { bottom: parent.bottom; left: parent.left; right: parent.right; leftMargin: 12; rightMargin: 12 } height: 6 }
    ResizeEdge { edges: Qt.TopEdge | Qt.LeftEdge; cursorShape: Qt.SizeFDiagCursor
                 anchors { top: parent.top; left: parent.left } width: 12; height: 12 }
    ResizeEdge { edges: Qt.TopEdge | Qt.RightEdge; cursorShape: Qt.SizeBDiagCursor
                 anchors { top: parent.top; right: parent.right } width: 12; height: 12 }
    ResizeEdge { edges: Qt.BottomEdge | Qt.LeftEdge; cursorShape: Qt.SizeBDiagCursor
                 anchors { bottom: parent.bottom; left: parent.left } width: 12; height: 12 }
    ResizeEdge { edges: Qt.BottomEdge | Qt.RightEdge; cursorShape: Qt.SizeFDiagCursor
                 anchors { bottom: parent.bottom; right: parent.right } width: 12; height: 12 }
}
