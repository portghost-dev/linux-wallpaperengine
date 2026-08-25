import QtQuick
import QtQuick.Controls.Basic
import "."

Rectangle {
    id: header

    property var engineStatus: ({})
    // raw `systemctl --user is-active` output for the master unit, not a boolean: the
    // status light needs to tell "failed" from "inactive" and "activating" from "active",
    // which a bool cannot carry.
    property string masterState: "absent"

    signal masterToggled(bool on)

    // live search query (as typed, not casefolded) - the library empty state echoes it in
    // its no-match copy. Mirrors the search field's text so the Library view,
    // which cannot reach into this subtree, can bind to it through Main.qml.
    readonly property string query: search.text

    height: 48
    color: Theme.base

    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: Theme.border
    }

    // Empty header space drags the frameless window. It must NEVER win a drag that started
    // inside a child that already took the press - dragging in the search field is text
    // selection, not a window move. DragHandler's default grabPermissions (246) include
    // CanTakeOverFromItems, which is exactly the permission to steal the TextField's grab
    // mid-drag; dropping that one flag leaves empty-space dragging untouched (no item holds
    // a grab there, so the handler activates normally).
    DragHandler {
        target: null
        grabPermissions: PointerHandler.CanTakeOverFromHandlersOfDifferentType
                       | PointerHandler.ApprovesTakeOverByHandlersOfSameType
                       | PointerHandler.ApprovesTakeOverByHandlersOfDifferentType
                       | PointerHandler.ApprovesTakeOverByItems
                       | PointerHandler.ApprovesCancellation
        onActiveChanged: if (active) header.Window.window.startSystemMove()
    }

    property string countsText: ""
    property bool workshopActive: false
    property string workshopCounts: ""    // "N new - M in library" (set by Main for the Workshop view)
    function refreshCounts() {
        var name = "";
        try { name = backend.activePlaylist().name || ""; } catch (e) { name = ""; }
        if (name.length > 20)
            name = name.substring(0, 20);
        countsText = backend.totalCount() + " wallpapers · "
                   + backend.playlistCount() + (name ? " in " + name : " in playlist");
    }
    Component.onCompleted: refreshCounts()
    Connections {
        target: backend
        function onCountChanged() { header.refreshCounts() }
        function onPlaylistsChanged() { header.refreshCounts() }
    }


    readonly property bool engineUp: (engineStatus.state || "") === "up"

    // `systemctl is-active` emits active / activating / reloading / deactivating / inactive /
    // failed / masked; models.masterState() adds "absent" when the unit is not installed.
    // INTENT is what the knob position means: a crashed unit was started by the user, so the
    // switch stays right and the track goes red - it is honest that it wants to run and cannot.
    readonly property bool intentOn: masterState === "active" || masterState === "activating"
                                  || masterState === "reloading" || masterState === "failed"

    readonly property string statusKind: {
        if (masterState === "failed")
            return "crashed";
        if (masterState === "activating" || masterState === "reloading")
            return "starting";
        if (masterState !== "active")
            return "off";
        return header.engineUp ? "running" : "starting";
    }

    readonly property color statusTrack: statusKind === "running" ? Theme.accent
                                       : statusKind === "starting" ? Theme.warning
                                       : statusKind === "crashed" ? Theme.danger
                                       : Theme.toggleOffTrack

    readonly property string statusText: {
        switch (statusKind) {
        case "running":  return "Engine running";
        case "starting": return masterState === "reloading" ? "Engine reloading" : "Engine starting";
        case "crashed":  return "Engine crashed. Check the Developer log";
        default:         return masterState === "absent" ? "Engine unit not installed" : "Engine off";
        }
    }


    // backend.engineStats(): /proc CPU delta across the engine family, whole-GPU utilization,
    // per-process VRAM. This USED to be gated on the cluster being visible; the rings are
    // permanent now, so the timer runs for the life of the app and the backend folds the GPU
    // and total-VRAM queries into one nvidia-smi fork to pay for it.
    property var liveStats: ({})
    Timer {
        interval: 3000
        repeat: true
        triggeredOnStart: true
        running: true
        onTriggered: header.liveStats = backend.engineStats()
    }

    function _num(v) { return (v === undefined || v === null) ? -1 : Number(v); }

    function cpuFrac() {
        var s = header.liveStats;
        return (s && s.pids > 0) ? Math.min(1, Math.max(0, s.cpu / 100)) : 0;
    }
    function cpuValue() {
        var s = header.liveStats;
        return (s && s.pids > 0) ? Math.round(s.cpu) + "%" : "-";
    }

    // --- GPU: WHOLE-GPU utilization, deliberately a different subject from the rest (it is
    // the only figure with a working source - per-PID sm% is a dud on this hardware). The
    // hover says so; see models._gpu_sample.
    function gpuValue() {
        var g = header._num(header.liveStats ? header.liveStats.gpu : undefined);
        return g >= 0 ? Math.round(g) + "%" : "-";
    }
    function gpuFrac() {
        var g = header._num(header.liveStats ? header.liveStats.gpu : undefined);
        return g >= 0 ? Math.min(1, g / 100) : 0;
    }

    function vramMb() {
        var s = header.liveStats;
        return (s && header._num(s.vram) >= 0) ? Number(s.vram) : -1;
    }
    function vramValue() {
        var mb = header.vramMb();
        return mb < 0 ? "-" : (mb + " MB");
    }
    function vramFrac() {
        var mb = header.vramMb();
        var t = header._num(header.liveStats ? header.liveStats.vramTotal : undefined);
        return (mb > 0 && t > 0) ? Math.min(1, mb / t) : 0;
    }

    // --- RAM: resident MB across the engine family, filled against the unit's MemoryHigh
    // (read from systemd at runtime, never hardcoded; no cap = the bar draws empty) ---
    function ramMb() {
        var s = header.liveStats;
        return (s && header._num(s.rss) >= 0) ? Number(s.rss) : -1;
    }
    function ramValue() {
        var mb = header.ramMb();
        return mb < 0 ? "-" : (mb + " MB");
    }
    function ramFrac() {
        var mb = header.ramMb();
        var cap = header._num(header.liveStats ? header.liveStats.memHigh : undefined);
        return (mb > 0 && cap > 0) ? Math.min(1, mb / cap) : 0;
    }

    // --- FPS: measured rate off the 2s status poll, filled against the engine's own cap ---
    function fpsValue() {
        var v = header.engineStatus.fps;
        return (v === undefined || v === null) ? "-" : String(v);
    }
    function fpsFrac() {
        var v = header._num(header.engineStatus.fps);
        var cap = header._num(header.engineStatus.fps_cap);
        // clamp at full when the measured rate exceeds the cap rather than overflowing the arc
        return (v >= 0 && cap > 0) ? Math.min(1, v / cap) : 0;
    }
    // FPS breaches LOW, not high - the meter is honest that dropping to half the cap is the
    // bad direction. Only counts once there is a real reading and a real cap.
    function fpsBreached() {
        var v = header._num(header.engineStatus.fps);
        var cap = header._num(header.engineStatus.fps_cap);
        return v >= 0 && cap > 0 && v < cap * 0.5;
    }


    Row {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: Theme.spacingLg
        spacing: Theme.spacingMd

        ThemedSwitch {
            id: masterSw
            anchors.verticalCenter: parent.verticalCenter
            pillWidth: 30
            pillHeight: 17
            checked: header.intentOn
            // `onAccent` is the legible-dark-on-a-coloured-fill token, so the knob reads on
            // success/warning/danger without reintroducing the F13 near-black-on-light bug
            // (that was caused by the THEMED textPrimary knob; this token is theme-stable).
            onTrackColor: header.statusTrack
            onKnobColor: Theme.onAccent
            onToggled: header.masterToggled(checked)

            // A Switch writes `checked` imperatively on click, which destroys the declarative
            // binding above - after one click the knob would never track the unit again, and
            // the crashed state (knob right, red track) is exactly the case that needs it.
            // Re-assert on every change of the underlying truth.
            Connections {
                target: header
                function onIntentOnChanged() { masterSw.checked = header.intentOn }
            }

            HoverHandler { id: masterHover }
            ToolTip.visible: masterHover.hovered
            ToolTip.text: header.statusText
            ToolTip.delay: 300
        }

        // rings out, five bars in - the ring shed ladder is retired; one face at all widths
        Row {
            id: meterRow
            anchors.verticalCenter: parent.verticalCenter
            spacing: 9

            BarMeter {
                anchors.verticalCenter: parent.verticalCenter
                fraction: header.cpuFrac()
                value: header.cpuValue()
                label: "CPU"
                breached: header.cpuFrac() > 0.85
            }
            BarMeter {
                anchors.verticalCenter: parent.verticalCenter
                fraction: header.gpuFrac()
                value: header.gpuValue()
                label: "GPU"
                breached: header.gpuFrac() > 0.90
            }
            BarMeter {
                anchors.verticalCenter: parent.verticalCenter
                fraction: header.ramFrac()
                value: header.ramValue()
                label: "RAM"
            }
            BarMeter {
                anchors.verticalCenter: parent.verticalCenter
                fraction: header.vramFrac()
                value: header.vramValue()
                label: "VRAM"
                breached: header.vramFrac() > 0.90
            }
            BarMeter {
                anchors.verticalCenter: parent.verticalCenter
                fraction: header.fpsFrac()
                value: header.fpsValue()
                label: "FPS"
                breached: header.fpsBreached()
            }
        }
    }


    Row {
        anchors.verticalCenter: parent.verticalCenter
        anchors.right: parent.right
        anchors.rightMargin: Theme.spacingLg
        spacing: Theme.spacingMd


        TextField {
            id: search
            width: Theme.usableWidth <= 560 ? 94 : 124
            height: 28
            anchors.verticalCenter: parent.verticalCenter
            placeholderText: "Search"
            color: Theme.textPrimary
            placeholderTextColor: Theme.textTertiary
            font.pixelSize: Theme.fontBody13
            leftPadding: searchMag.x + searchMag.width + Theme.spacingSm
            // the clear-x's lane is reserved whether or not it shows, so the first typed
            // character does not shove the whole string left when the x appears
            rightPadding: Theme.spacingMd + clearX.width
            verticalAlignment: TextInput.AlignVCenter
            background: Rectangle {
                color: Theme.surface
                radius: Theme.radiusSm
                border.width: 1
                border.color: search.activeFocus ? Theme.borderStrong : Theme.border
            }
            onTextEdited: backend.setSearch(text)

            IconSearch {
                id: searchMag
                size: 13
                color: Theme.textTertiary
                x: Theme.spacingMd - 2   // canvas padding-left 10 (spacingMd is 12; -2 = 10)
                anchors.verticalCenter: parent.verticalCenter
            }

            // clear-x: bare glyph, no plate - the gray 28x28 button grammar belongs to the
            // modal/palette closes, not to an in-field affordance. Present only once there is
            // something to clear. The 20x20 Item is an invisible hit target: a 12px glyph is
            // a miserable click, and padding the target costs nothing visually.
            Item {
                id: clearX
                objectName: "searchClear"
                width: 20; height: 20
                visible: search.text !== ""
                anchors.right: parent.right
                anchors.rightMargin: Theme.spacingMd - 6
                anchors.verticalCenter: parent.verticalCenter
                IconX {
                    anchors.centerIn: parent
                    size: 12
                    color: clearHov.hovered ? Theme.textPrimary : Theme.textSecondary
                }
                HoverHandler { id: clearHov; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        search.clear();
                        // onTextEdited fires for typing only - a programmatic clear has to
                        // push the empty query itself or the grid keeps the old filter
                        backend.setSearch("");
                        search.forceActiveFocus();
                    }
                }
            }
        }

        Item {
            id: funnelBtn
            width: 28; height: 28
            anchors.verticalCenter: parent.verticalCenter
            property bool filtersActive: typeGroup.value !== "all" || plGroup.value !== "any"
            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSm
                color: Theme.surface
                border.width: 1
                border.color: Theme.border
            }
            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSm
                color: Theme.hoverWash
                opacity: (funnelPop.visible || funnelHover.hovered) ? 1 : 0
            }
            Column {
                anchors.centerIn: parent
                spacing: 3
                Rectangle { width: 12; height: 2; radius: 1; color: Theme.textSecondary; anchors.horizontalCenter: parent.horizontalCenter }
                Rectangle { width: 8;  height: 2; radius: 1; color: Theme.textSecondary; anchors.horizontalCenter: parent.horizontalCenter }
                Rectangle { width: 4;  height: 2; radius: 1; color: Theme.textSecondary; anchors.horizontalCenter: parent.horizontalCenter }
            }
            Rectangle {
                width: 6; height: 6; radius: 3
                anchors.top: parent.top
                anchors.right: parent.right
                color: Theme.accent
                visible: funnelBtn.filtersActive
            }
            HoverHandler { id: funnelHover }
            TapHandler {
                // same press-outside-then-reopen race as the strip menus: by tap time the
                // popup already closed itself, so gate the reopen on the justClosed window.
                onTapped: {
                    if (funnelPop.visible) funnelPop.close();
                    else if (!funnelPop.justClosed) funnelPop.open();
                }
            }

            Popup {
                id: funnelPop
                x: width > funnelBtn.width ? funnelBtn.width - width : 0
                y: funnelBtn.height + 4
                padding: Theme.spacingMd
                property bool justClosed: false
                onClosed: { justClosed = true; funnelGuard.restart() }
                Timer { id: funnelGuard; interval: 150; onTriggered: funnelPop.justClosed = false }
                background: Rectangle {
                    color: Theme.surfaceVariant
                    radius: Theme.radiusMd
                    border.width: 1
                    border.color: Theme.borderStrong
                }
                contentItem: Column {
                    spacing: Theme.spacingSm

                    component FilterGroup: Column {
                        id: group
                        property string title: ""
                        property var options: []   // [{v, label}]
                        property string value: ""
                        signal picked(string v)
                        spacing: Theme.spacingXs
                        Label {
                            text: group.title
                            color: Theme.textTertiary
                            font.pixelSize: Theme.fontMeta
                        }
                        Repeater {
                            model: group.options
                            delegate: Item {
                                id: opt
                                required property var modelData
                                width: 140; height: 24
                                Rectangle {
                                    anchors.fill: parent
                                    radius: Theme.radiusXs
                                    color: group.value === opt.modelData.v ? Theme.selectionWash
                                         : optHover.hovered ? Theme.hoverWash : "transparent"
                                }
                                Label {
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.left: parent.left
                                    anchors.leftMargin: Theme.spacingSm
                                    text: opt.modelData.label
                                    color: group.value === opt.modelData.v ? Theme.textPrimary : Theme.textSecondary
                                    font.pixelSize: Theme.fontControl
                                }
                                HoverHandler { id: optHover }
                                TapHandler { onTapped: group.picked(opt.modelData.v) }
                            }
                        }
                    }

                    FilterGroup {
                        id: typeGroup
                        title: "Type"
                        value: "all"
                        options: [{v: "all", label: "All"}, {v: "scene", label: "Scenes"},
                                  {v: "video", label: "Videos"}]
                        // setTypeFilter/setPlaylistFilter live on the filter model, not
                        // backend itself (grep models.py: LibraryFilterModel owns them) -
                        // these were dead calls to a nonexistent backend.* method before.
                        onPicked: function(v) { value = v; backend.filterModel.setTypeFilter(v) }
                    }
                    FilterGroup {
                        id: plGroup
                        title: "In playlist"
                        value: "any"
                        options: [{v: "any", label: "Any"}, {v: "in", label: "In playlist"},
                                  {v: "out", label: "Not in playlist"}]
                        onPicked: function(v) { value = v; backend.filterModel.setPlaylistFilter(v) }
                    }
                }
            }
        }

        // Window close: always present, same F9 face as the funnel beside it. On every
        // compositor the X routes through the normal window close, so close-to-tray
        // decides whether it hides to the tray or ends the app - one behavior everywhere.
        Item {
            id: closeBtn
            objectName: "headerClose"
            width: 28; height: 28
            anchors.verticalCenter: parent.verticalCenter
            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusXs
                color: Theme.surface
                border.width: 1
                border.color: closeHover.hovered ? Theme.borderStrong : Theme.border
            }
            IconX {
                anchors.centerIn: parent
                size: 12
                color: closeHover.hovered ? Theme.textPrimary : Theme.textSecondary
            }
            HoverHandler { id: closeHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: closeBtn.Window.window.close() }
        }
    }
}
