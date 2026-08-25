import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import "."

Window {
    id: pal
    width: 430
    height: 420
    minimumWidth: 430
    minimumHeight: 420
    flags: Qt.Tool | Qt.FramelessWindowHint | (pinned ? Qt.WindowStaysOnTopHint : 0)
    color: Theme.surface
    title: "Developer tools"

    property bool pinned: true
    property int tab: 0
    property int rev: 0
    property bool _restored: false   // gate saves until the restore pass has run

    // the bench target name for the titlebar context ("Developer tools <em dash> <scene>"). Set by
    // the cockpit (DevView) from its target combo, which is the only place that knows the display
    // name; "" (default) shows the bare title with no context suffix, an honest empty state.
    property string targetName: ""

    Connections {
        target: dev
        function onStateChanged() { pal.rev++ }
        function onRunsChanged() { pal.rev++ }
    }

    // persist geometry / pin / tab across app restarts (design 17). Restore on open, then save
    // (debounced) on any change. The _restored gate stops the restore itself from re-saving.
    Component.onCompleted: {
        var s = dev.paletteState();
        if (s.width) pal.width = s.width;
        if (s.height) pal.height = s.height;
        if (s.x !== undefined) pal.x = s.x;
        if (s.y !== undefined) pal.y = s.y;
        if (s.pinned !== undefined) pal.pinned = s.pinned;
        if (s.tab !== undefined) pal.tab = s.tab;
        if (s.refPath !== undefined) refTab.refPath = s.refPath;
        pal._restored = true;
    }
    Timer {
        id: saveState
        interval: 400
        onTriggered: dev.savePaletteState({
            "x": pal.x, "y": pal.y, "width": pal.width, "height": pal.height,
            "pinned": pal.pinned, "tab": pal.tab, "refPath": refTab.refPath
        })
    }
    function _persist() { if (pal._restored) saveState.restart(); }
    onXChanged: _persist()
    onYChanged: _persist()
    onWidthChanged: _persist()
    onHeightChanged: _persist()
    onPinnedChanged: { _persist(); dev.setPalettePinned(pal.pinned); }
    onTabChanged: _persist()

    Rectangle {
        id: bar
        width: parent.width
        height: 32
        color: Theme.surfaceVariant
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
        DragHandler { target: null; onActiveChanged: if (active) pal.startSystemMove() }
        Row {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: Theme.spacingMd
            spacing: Theme.spacingSm
            Row {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2.5
                Repeater {
                    model: 3
                    delegate: Rectangle { width: 3; height: 3; radius: 2; color: Theme.textTertiary
                        anchors.verticalCenter: parent.verticalCenter }
                }
            }
            Label {
                anchors.verticalCenter: parent.verticalCenter
                text: "Developer tools"
                color: Theme.textPrimary
                font.pixelSize: Theme.fontControl
                font.weight: Theme.weightMedium
            }
            Label {
                anchors.verticalCenter: parent.verticalCenter
                visible: pal.targetName !== ""
                text: "- " + pal.targetName
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMeta
            }
        }
        Row {
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: parent.right
            anchors.rightMargin: 6
            spacing: Theme.spacingXs
            Rectangle {
                width: 24; height: 24; radius: 5
                color: Theme.activeWash
                anchors.verticalCenter: parent.verticalCenter
                IconPin {
                    anchors.centerIn: parent
                    size: 11
                    color: pal.pinned ? Theme.accent : Theme.textTertiary
                }
                TapHandler { onTapped: pal.pinned = !pal.pinned }
            }
            Rectangle {
                width: 24; height: 24; radius: 5
                color: closeHov.hovered ? Theme.hoverWash : "transparent"
                anchors.verticalCenter: parent.verticalCenter
                IconX {
                    anchors.centerIn: parent
                    size: 11
                    color: Theme.textSecondary
                }
                HoverHandler { id: closeHov }
                TapHandler { onTapped: pal.close() }
            }
        }
    }

    SegmentControl {
        id: seg
        anchors.top: bar.bottom
        anchors.topMargin: Theme.spacingSm
        anchors.horizontalCenter: parent.horizontalCenter
        model: ["Isolator", "A/B", "Region", "Ref", "Verdict"]
        currentIndex: pal.tab
        onActivated: pal.tab = index
    }

    StackLayout {
        anchors.top: seg.bottom
        anchors.topMargin: Theme.spacingSm
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Theme.spacingMd
        currentIndex: pal.tab

        Item {
            id: isoTab
            property var objs: (pal.rev, dev.objectList())
            property var iso: (pal.rev, dev.isolationState())
            property string typeFilter: "All"
            property int rawOpen: -1

            function presentTypes() {
                var seen = [];
                for (var i = 0; i < objs.length; i++) {
                    var t = String(objs[i].type || "");
                    if (t !== "" && seen.indexOf(t) < 0) seen.push(t);
                }
                return seen;
            }
            function matchesFilter(o) {
                if (typeFilter === "All") return true;
                return String(o.type || "").toLowerCase() === typeFilter.toLowerCase();
            }
            function matchesSearch(o) {
                if (isoSearch.text === "") return true;
                return (String(o.name) + " " + String(o.objid)).toLowerCase()
                       .indexOf(isoSearch.text.toLowerCase()) >= 0;
            }

            Column {
                anchors.fill: parent
                spacing: 0

                Row {
                    width: parent.width
                    height: 20
                    spacing: Theme.spacingSm
                    Label {
                        text: "Object Isolator"
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                        font.weight: Theme.weightMedium
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Label {
                        // scene context per 9e: "<scene> <middle dot> N objects"; the scene name
                        // drops out cleanly when no target name has been forwarded (honest empty).
                        // mode matters here: "live" means solo/skip act on the RUNNING
                        // wallpaper instantly, "bench" means they compose the held child's
                        // argv. An empty list in live mode is usually honest rather than
                        // broken - video and web wallpapers have no scene graph to isolate.
                        text: {
                            var mode = (pal.rev, dev.isolationMode());
                            var head = (pal.targetName !== "" ? pal.targetName + " · " : "· ");
                            if (mode === "live" && isoTab.objs.length === 0)
                                return head + "no objects to isolate (video or web wallpaper)";
                            return head + isoTab.objs.length + " objects"
                                   + (mode === "live" ? " · live" : mode === "bench" ? " · bench" : "");
                        }
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMicro
                        anchors.verticalCenter: parent.verticalCenter
                        elide: Text.ElideRight
                    }
                    Item { width: parent.width - x - clearChip.width - Theme.spacingSm * 2; height: 1 }
                    Rectangle {
                        id: clearChip
                        height: 20
                        width: clearLbl.implicitWidth + Theme.spacingMd
                        radius: Theme.radiusSm
                        color: clearHov.hovered ? Theme.hoverWash : "transparent"
                        border.width: 1; border.color: Theme.border
                        anchors.verticalCenter: parent.verticalCenter
                        Label { id: clearLbl; anchors.centerIn: parent; text: "Clear"
                                color: Theme.textSecondary; font.pixelSize: Theme.fontMeta }
                        HoverHandler { id: clearHov }
                        TapHandler { onTapped: dev.clearIsolation() }
                    }
                }

                Flow {
                    width: parent.width
                    topPadding: 7
                    spacing: 5
                    Rectangle {
                        height: 20
                        width: allLbl.implicitWidth + Theme.spacingLg
                        radius: Theme.radiusSm
                        color: isoTab.typeFilter === "All" ? Theme.segmentWash
                             : allChipHov.hovered ? Theme.hoverWash : "transparent"
                        Label { id: allLbl; anchors.centerIn: parent; text: "All"
                                color: isoTab.typeFilter === "All" ? Theme.textPrimary : Theme.textTertiary
                                font.pixelSize: Theme.fontMeta }
                        HoverHandler { id: allChipHov }
                        TapHandler { onTapped: isoTab.typeFilter = "All" }
                    }
                    Repeater {
                        model: (pal.rev, isoTab.presentTypes())
                        delegate: Rectangle {
                            required property string modelData
                            height: 20
                            width: chLbl.implicitWidth + Theme.spacingLg
                            radius: Theme.radiusSm
                            property bool sel: isoTab.typeFilter === modelData
                            color: sel ? Theme.segmentWash : chHov.hovered ? Theme.hoverWash : "transparent"
                            Label {
                                id: chLbl; anchors.centerIn: parent
                                text: modelData.charAt(0).toUpperCase() + modelData.slice(1)
                                color: parent.sel ? Theme.textPrimary : Theme.textTertiary
                                font.pixelSize: Theme.fontMeta
                            }
                            HoverHandler { id: chHov }
                            TapHandler { onTapped: isoTab.typeFilter = modelData }
                        }
                    }
                }

                Item { width: 1; height: 7 }

                TextField {
                    id: isoSearch
                    width: parent.width; height: 24
                    topPadding: 0; bottomPadding: 0
                    leftPadding: 9
                    placeholderText: "name or id"
                    color: Theme.textPrimary; font.pixelSize: Theme.fontMeta
                    font.family: Theme.monoFamily
                    background: Rectangle { color: Theme.inputWell; radius: Theme.radiusSm
                        border.width: 1; border.color: parent.activeFocus ? Theme.borderStrong : Theme.border }
                }

                ListView {
                    id: isoList
                    width: parent.width
                    height: parent.height - y - footer.height
                    topMargin: 5
                    clip: true
                    ScrollBar.vertical: ScrollBar {}
                    model: {
                        var out = [];
                        for (var i = 0; i < isoTab.objs.length; i++) {
                            var o = isoTab.objs[i];
                            if (isoTab.matchesFilter(o) && isoTab.matchesSearch(o)) out.push(o);
                        }
                        return out;
                    }
                    delegate: Column {
                        id: rowCol
                        required property var modelData
                        required property int index
                        // rows stop short of the scrollbar band so the chips are never
                        // under it (clicks in that band were hitting the scrollbar)
                        width: isoList.width - 12
                        property bool isEffect: String(modelData.type || "").toLowerCase() === "effect"
                        // solo is a SET: a row is soloed when its id is in it, and S toggles
                        // that one row's membership without disturbing the others.
                        property bool soloed: !isEffect
                            && (pal.rev, dev.isolationState().soloObjects
                                        .indexOf(String(modelData.objid)) >= 0)
                        property bool skipped: (pal.rev, dev.isolationState().skipObjects
                                               .indexOf(String(modelData.objid)) >= 0)
                        property bool rawOpen: isoTab.rawOpen === index

                        Item {
                            width: parent.width
                            height: 27
                            opacity: rowCol.skipped ? 0.5 : 1.0

                            Rectangle {
                                anchors.fill: parent
                                color: rowCol.soloed ? Theme.selectionWash
                                     : rowHov.hovered ? Theme.hoverWash : "transparent"
                            }
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width
                                        height: 1; color: Theme.hoverWash }

                            Row {
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.right: chips.left
                                spacing: 9
                                Label {
                                    width: 22
                                    text: rowCol.isEffect ? "" : String(rowCol.modelData.objid)
                                    color: Theme.textTertiary
                                    font.pixelSize: Theme.fontMicro
                                    font.family: Theme.monoFamily
                                    elide: Text.ElideRight
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                Row {
                                    width: parent.width - 22 - 54 - parent.spacing * 2
                                    spacing: 6
                                    anchors.verticalCenter: parent.verticalCenter
                                    Rectangle {
                                        visible: rowCol.isEffect
                                        width: 8; height: 1; color: Theme.borderStrong
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    Label {
                                        text: rowCol.modelData.name || rowCol.modelData.objid
                                        color: rowCol.soloed ? Theme.textPrimary : Theme.textMutedBody
                                        font.pixelSize: Theme.fontMeta
                                        font.family: Theme.monoFamily
                                        elide: Text.ElideRight
                                        width: parent.width - (rowCol.isEffect ? 14 : 0)
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                                Label {
                                    width: 54
                                    text: rowCol.modelData.type
                                    color: Theme.textTertiary
                                    font.pixelSize: Theme.fontMicro
                                    elide: Text.ElideRight
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }

                            Row {
                                id: chips
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.right: parent.right
                                spacing: 4
                                Rectangle {
                                    id: soloChip
                                    visible: !rowCol.isEffect
                                    width: 17; height: 17; radius: Theme.radiusXs
                                    color: rowCol.soloed ? Theme.accent : "transparent"
                                    border.width: rowCol.soloed ? 0 : 1
                                    border.color: Theme.border
                                    Label {
                                        anchors.centerIn: parent
                                        text: "S"
                                        color: rowCol.soloed ? Theme.onAccent : Theme.textTertiary
                                        font.pixelSize: 10
                                        font.weight: rowCol.soloed ? Theme.weightMedium : Theme.weightRegular
                                    }
                                    TapHandler {
                                        onTapped: dev.solo(rowCol.modelData.objid)
                                    }
                                }
                                // skip chip (circled slash); its tap handler lives IN the chip
                                // so its bounds are the chip (a handler's bounds come from its
                                // parent - `target:` does not scope them)
                                Rectangle {
                                    id: skipChip
                                    width: 17; height: 17; radius: Theme.radiusXs
                                    color: rowCol.skipped ? Theme.dangerWash : "transparent"
                                    border.width: 1
                                    border.color: rowCol.skipped ? Theme.danger : Theme.border
                                    Label {
                                        anchors.centerIn: parent
                                        text: "⊘"
                                        color: rowCol.skipped ? Theme.danger : Theme.textTertiary
                                        font.pixelSize: 10
                                    }
                                    TapHandler {
                                        onTapped: dev.setSkipObject(rowCol.modelData.objid,
                                                                    !rowCol.skipped)
                                    }
                                }
                            }

                            HoverHandler { id: rowHov }
                            // row gesture layer: click = solo, shift-click = skip,
                            // double-click = raw JSON (design 9e footer legend).
                            // TapHandlers hold PASSIVE grabs, so the row layer also fires for
                            // taps on the chips - a chip click then toggled twice (solo on+off:
                            // "cannot select solo"), and a skip-chip click fired skip AND row-
                            // solo together (gray screen). Taps over the chip zone belong to
                            // the chips alone.
                            property real chipZoneX: chips.x - 4
                            TapHandler {
                                acceptedModifiers: Qt.NoModifier
                                onTapped: (pt) => {
                                    if (pt.position.x >= parent.chipZoneX) return;
                                    if (rowCol.isEffect) return;
                                    dev.solo(rowCol.modelData.objid);
                                }
                                onDoubleTapped: (pt) => {
                                    if (pt.position.x >= parent.chipZoneX) return;
                                    isoTab.rawOpen = (rowCol.rawOpen ? -1 : rowCol.index);
                                }
                            }
                            TapHandler {
                                acceptedModifiers: Qt.ShiftModifier
                                onTapped: (pt) => {
                                    if (pt.position.x >= parent.chipZoneX) return;
                                    dev.setSkipObject(rowCol.modelData.objid, !rowCol.skipped);
                                }
                            }
                            // NOTE: a pointer handler's tap bounds come from its PARENT, not
                            // its `target` (target is the item a handler MANIPULATES, e.g. for
                            // drags). A `TapHandler { target: skipChip }` declared here fired
                            // for EVERY row tap - which is how a click anywhere on the row set
                            // solo AND skip together. The skip chip's direct handler now lives
                            // inside the chip itself, next to the solo chip's.
                        }

                        Rectangle {
                            visible: rowCol.rawOpen
                            width: parent.width
                            height: visible ? rawText.implicitHeight + Theme.spacingSm * 2 : 0
                            color: Theme.inputWell
                            border.width: 1; border.color: Theme.border
                            radius: Theme.radiusXs
                            Label {
                                id: rawText
                                anchors.fill: parent
                                anchors.margins: Theme.spacingSm
                                text: JSON.stringify(rowCol.modelData, null, 2)
                                color: Theme.textMutedBody
                                font.pixelSize: Theme.fontMicro
                                font.family: Theme.monoFamily
                                wrapMode: Text.WrapAnywhere
                            }
                        }
                    }

                    Label {
                        anchors.centerIn: parent
                        visible: isoList.count === 0
                        width: parent.width - Theme.spacingLg * 2
                        horizontalAlignment: Text.AlignHCenter
                        text: isoTab.objs.length === 0
                              ? "No enumerable objects for this target"
                              : "No objects match the filter"
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMeta
                        wrapMode: Text.WordWrap
                    }
                }

                Item {
                    id: footer
                    width: parent.width
                    height: 30
                    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.border }
                    Label {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width
                        text: "Click = solo (toggles, pick as many as you want) · shift-click = skip " +
                              "· double-click = raw JSON · persists across tabs + relaunches"
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMicro
                        elide: Text.ElideRight
                    }
                }
            }
        }

        Item {
            id: abTab
            property bool envOpen: false
            property bool live: (pal.rev, dev.abRunning())
            function fmtMMSS(secs) {
                var m = Math.floor(secs / 60);
                var ss = secs % 60;
                return (m < 10 ? "0" : "") + m + ":" + (ss < 10 ? "0" : "") + ss;
            }
            Column {
                anchors.fill: parent
                anchors.topMargin: -2
                anchors.leftMargin: 2
                anchors.rightMargin: 2
                spacing: 0

                Item {
                    width: parent.width
                    height: 26
                    Label {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.right: verbBtns.left
                        anchors.rightMargin: Theme.spacingSm
                        textFormat: Text.StyledText
                        text: "<b>A/B Comparison:</b> Select or enter differences, and Launch."
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontMeta
                        elide: Text.ElideRight
                    }
                    Row {
                        id: verbBtns
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.spacingSm
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: resetLbl.implicitWidth + Theme.spacingLg; height: 24
                            radius: Theme.radiusSm
                            color: resetHov.hovered ? Theme.hoverWash : "transparent"
                            border.width: 1; border.color: Theme.border
                            Label { id: resetLbl; anchors.centerIn: parent; text: "Reset to stock"
                                    color: Theme.textSecondary; font.pixelSize: Theme.fontMeta }
                            HoverHandler { id: resetHov }
                            TapHandler { onTapped: dev.abReset() }
                        }
                        Rectangle {
                            objectName: "abPrimarySlot"
                            anchors.verticalCenter: parent.verticalCenter
                            width: 92; height: 24
                            radius: Theme.radiusSm
                            color: abTab.live ? Theme.danger : Theme.accent
                            Label {
                                anchors.centerIn: parent
                                text: abTab.live ? "Stop A/B" : "Launch A/B"
                                color: Theme.onAccent
                                font.pixelSize: Theme.fontMeta
                                font.weight: Theme.weightMedium
                            }
                            TapHandler { onTapped: dev.abRunning() ? dev.stopAB() : dev.startAB() }
                        }
                    }
                }

                Item { width: 1; height: 8 }

                Item {
                    width: parent.width
                    height: 14
                    Row {
                        anchors.right: parent.right
                        anchors.rightMargin: 0
                        spacing: 10
                        Item {
                            width: 30; height: 14
                            Row { anchors.centerIn: parent; spacing: 3
                                Rectangle { width: 6; height: 6; radius: 3; color: Theme.accent
                                            anchors.verticalCenter: parent.verticalCenter }
                                Label { text: "A"; color: Theme.textSecondary
                                        font.pixelSize: Theme.fontMicro
                                        anchors.verticalCenter: parent.verticalCenter } }
                        }
                        Item {
                            width: 30; height: 14
                            Row { anchors.centerIn: parent; spacing: 3
                                Rectangle { width: 6; height: 6; radius: 3; color: Theme.warning
                                            anchors.verticalCenter: parent.verticalCenter }
                                Label { text: "B"; color: Theme.textSecondary
                                        font.pixelSize: Theme.fontMicro
                                        anchors.verticalCenter: parent.verticalCenter } }
                        }
                    }
                }

                Column {
                    id: matrixCol
                    width: parent.width
                    Repeater {
                        model: (pal.rev, dev.ourToggles())
                        delegate: Item {
                            id: fixRow
                            required property var modelData
                            width: matrixCol.width
                            height: 22
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width
                                        height: 1; color: Theme.hoverWash }
                            Label {
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - 130
                                text: fixRow.modelData.what
                                color: Theme.textPrimary
                                font.pixelSize: Theme.fontControl
                                elide: Text.ElideRight
                            }
                            Row {
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 10
                                Item {
                                    width: 30; height: fixRow.height
                                    ThemedSwitch {
                                        anchors.fill: parent
                                        pillWidth: 24; pillHeight: 14
                                        checked: (pal.rev, dev.abFixOn("A", fixRow.modelData.key))
                                        onToggled: dev.setABFix("A", fixRow.modelData.key, checked)
                                    }
                                }
                                Item {
                                    width: 30; height: fixRow.height
                                    ThemedSwitch {
                                        anchors.fill: parent
                                        pillWidth: 24; pillHeight: 14
                                        checked: (pal.rev, dev.abFixOn("B", fixRow.modelData.key))
                                        onToggled: dev.setABFix("B", fixRow.modelData.key, checked)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Column {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: 2
                anchors.rightMargin: 2
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 22
                spacing: 6
                Item {
                    width: parent.width
                    height: 22
                    Rectangle { anchors.top: parent.top; width: parent.width; height: 1
                                color: Theme.hoverWash }
                    Row {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 6
                        IconChevron {
                            direction: abTab.envOpen ? "down" : "right"
                            size: 10; color: abTab.envOpen ? Theme.textPrimary : Theme.textSecondary
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Label {
                            text: "Extra env - per side"
                            color: abTab.envOpen ? Theme.textPrimary : Theme.textSecondary
                            font.pixelSize: Theme.fontControl
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Label {
                        visible: !abTab.envOpen
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        property int nset: {
                            var a = (pal.rev, dev.abEnvText("A")), b = dev.abEnvText("B");
                            var n = 0;
                            if (a.length) n += a.split("\n").length;
                            if (b.length) n += b.split("\n").length;
                            return n;
                        }
                        text: "KEY=VALUE" + (nset > 0 ? " \u00b7 " + nset + " set" : "")
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMicro
                        font.family: Theme.monoFamily
                    }
                    Label {
                        visible: abTab.envOpen
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: "queued \u00b7 applies on relaunch"
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMicro
                    }
                    TapHandler { onTapped: abTab.envOpen = !abTab.envOpen }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }

                Item { width: 1; height: abTab.envOpen ? 6 : 0 }
                Row {
                    width: parent.width
                    visible: abTab.envOpen
                    spacing: 10
                    Repeater {
                        model: ["A", "B"]
                        delegate: Column {
                            id: envCol
                            required property string modelData
                            width: (parent.width - 10) / 2
                            spacing: 3
                            Row {
                                height: 14
                                spacing: 4
                                Rectangle {
                                    width: 6; height: 6; radius: 3
                                    anchors.verticalCenter: parent.verticalCenter
                                    color: envCol.modelData === "A" ? Theme.accent : Theme.warning
                                }
                                Label { text: envCol.modelData; color: Theme.textSecondary
                                        font.pixelSize: Theme.fontMicro
                                        anchors.verticalCenter: parent.verticalCenter }
                            }
                            TextArea {
                                width: parent.width
                                height: 52
                                color: Theme.textMutedBody
                                font.pixelSize: Theme.fontMeta
                                font.family: Theme.monoFamily
                                placeholderText: "KEY=VALUE per line"
                                placeholderTextColor: Theme.textTertiary
                                background: Rectangle {
                                    color: Theme.inputWell
                                    radius: 5
                                    border.width: 1
                                    border.color: parent.activeFocus ? Theme.borderStrong : Theme.border
                                }
                                Component.onCompleted: text = dev.abEnvText(envCol.modelData)
                                onActiveFocusChanged: {
                                    if (!activeFocus) dev.setABEnvText(envCol.modelData, text);
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                objectName: "abAlertStripe"
                visible: abTab.live
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.leftMargin: -Theme.spacingMd
                anchors.rightMargin: -Theme.spacingMd
                anchors.bottomMargin: -Theme.spacingMd
                height: 26
                color: Theme.warningWash
                Rectangle { anchors.top: parent.top; width: parent.width; height: 1
                            color: Theme.warning }
                property int tick: 0
                Timer { interval: 1000; running: abTab.live; repeat: true
                        onTriggered: parent.tick++ }
                Row {
                    anchors.centerIn: parent
                    spacing: 6
                    Rectangle { width: 6; height: 6; radius: 3; color: Theme.warning
                                anchors.verticalCenter: parent.verticalCenter }
                    Label {
                        property int up: (parent.parent.tick, dev.uptimeSeconds())
                        text: "A/B live \u00b7 " + abTab.fmtMMSS(up)
                              + " - keep focus here; focused windows may brighten"
                        color: Theme.warning
                        font.pixelSize: Theme.fontMicro
                        font.weight: Theme.weightMedium
                        anchors.verticalCenter: parent.verticalCenter
                        elide: Text.ElideRight
                    }
                }
            }
        }

        Item {
            id: regionTab
            Column {
                anchors.fill: parent
                spacing: Theme.spacingSm
                Label {
                    width: parent.width
                    text: "Drag a rectangle on the live render, or set it numerically:"
                    color: Theme.textTertiary; font.pixelSize: Theme.fontMicro; wrapMode: Text.WordWrap
                }
                Row {
                    width: parent.width
                    spacing: Theme.spacingSm
                    Repeater {
                        model: ["x", "y", "w", "h"]
                        delegate: Column {
                            required property string modelData
                            spacing: 3
                            Label { text: modelData; color: Theme.textTertiary; font.pixelSize: Theme.fontMicro }
                            TextField {
                                objectName: "region_" + modelData
                                width: 58; height: 24
                                topPadding: 0; bottomPadding: 0
                                horizontalAlignment: Text.AlignRight
                                color: Theme.textMutedBody; font.pixelSize: Theme.fontMeta
                                font.family: Theme.monoFamily
                                validator: IntValidator { bottom: 0; top: 8192 }
                                background: Rectangle { color: Theme.inputWell; radius: Theme.radiusSm
                                    border.width: 1; border.color: parent.activeFocus ? Theme.borderStrong : Theme.border }
                            }
                        }
                    }
                    // Redraw (anatomy-only; no capture backend)
                    Rectangle {
                        anchors.bottom: parent.bottom
                        objectName: "regionRedraw"
                        enabled: false
                        opacity: 0.5
                        width: redrawLbl.implicitWidth + Theme.spacingLg; height: 24
                        radius: Theme.radiusSm
                        color: Theme.surfaceVariant
                        border.width: 1; border.color: Theme.border
                        Label { id: redrawLbl; anchors.centerIn: parent; text: "Redraw"
                                color: Theme.textPrimary; font.pixelSize: Theme.fontMeta }
                    }
                }
                Label {
                    text: "Readout - scene-FBO space · OBJPROBE"
                    color: Theme.textSecondary; font.pixelSize: Theme.fontMeta; font.weight: Theme.weightMedium
                    topPadding: 6
                }
                // Luminance bar + value. The separator sits OUTSIDE the Row: a Row is a
                // positioner, so a full-width first child would shove the labels a full
                // row-width to the right (same bug as the lens column header).
                Item {
                    width: parent.width
                    height: 28
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hoverWash }
                    Row {
                        anchors.fill: parent
                        spacing: 10
                        Label { width: 80; text: "Luminance"; color: Theme.textPrimary; font.pixelSize: Theme.fontMeta
                                anchors.verticalCenter: parent.verticalCenter }
                        Rectangle {
                            width: parent.width - 80 - 40 - 20; height: 3; radius: 1.5
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.border
                            Rectangle { width: 0; height: parent.height; radius: 1.5; color: Theme.accent }
                        }
                        Label { width: 40; text: "-"; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                                font.family: Theme.monoFamily; horizontalAlignment: Text.AlignRight
                                anchors.verticalCenter: parent.verticalCenter }
                    }
                }
                // RGBA mean (mono; separator outside the Row for the same reason)
                Item {
                    width: parent.width
                    height: 28
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hoverWash }
                    Row {
                        anchors.fill: parent
                        spacing: 10
                        Label { width: 80; text: "RGBA mean"; color: Theme.textPrimary; font.pixelSize: Theme.fontMeta
                                anchors.verticalCenter: parent.verticalCenter }
                        Label { text: "-"; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                                font.family: Theme.monoFamily; anchors.verticalCenter: parent.verticalCenter }
                    }
                }
                Row {
                    width: parent.width
                    height: 28
                    spacing: 10
                    visible: refTab.refPath !== ""
                    Label { width: 80; text: "Delta vs ref"; color: Theme.textPrimary; font.pixelSize: Theme.fontMeta
                            anchors.verticalCenter: parent.verticalCenter }
                    Rectangle {
                        width: parent.width - 80 - 40 - 20; height: 3; radius: 1.5
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.border
                        Rectangle { width: 0; height: parent.height; radius: 1.5; color: Theme.warning }
                    }
                    Label { width: 40; text: "-"; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                            font.family: Theme.monoFamily; horizontalAlignment: Text.AlignRight
                            anchors.verticalCenter: parent.verticalCenter }
                }
                Label {
                    text: "Delta row appears only while a reference is docked (Ref tab)."
                    color: Theme.textTertiary; font.pixelSize: Theme.fontMicro
                }
                Item {
                    width: parent.width
                    height: 26
                    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.border }
                    Label {
                        anchors.left: parent.left; anchors.bottom: parent.bottom
                        text: "Region persists per scene · shared by Ref Delta and the verdict context"
                        color: Theme.textTertiary; font.pixelSize: Theme.fontMicro; elide: Text.ElideRight
                        width: parent.width
                    }
                }
            }
        }

        Item {
            id: refTab
            property string refPath: ""
            function baseName(p) {
                if (!p) return "";
                var s = String(p).replace(/^file:\/\//, "");
                var i = s.lastIndexOf("/");
                return i >= 0 ? s.slice(i + 1) : s;
            }
            Column {
                anchors.fill: parent
                spacing: Theme.spacingSm

                Row {
                    width: parent.width
                    height: 22
                    spacing: Theme.spacingSm
                    Label {
                        width: parent.width - corpusLbl.width - changeChip.width - Theme.spacingSm * 2
                        text: refTab.refPath === "" ? "no reference selected" : refTab.baseName(refTab.refPath)
                        color: refTab.refPath === "" ? Theme.textTertiary : Theme.textSecondary
                        font.pixelSize: Theme.fontMeta
                        font.family: Theme.monoFamily
                        elide: Text.ElideRight
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Label { id: corpusLbl; text: "corpus"; color: Theme.textTertiary
                            font.pixelSize: Theme.fontMicro; anchors.verticalCenter: parent.verticalCenter }
                    Rectangle {
                        id: changeChip
                        height: 22; width: changeLbl.implicitWidth + Theme.spacingMd
                        radius: Theme.radiusSm
                        color: changeHov.hovered ? Theme.hoverWash : "transparent"
                        border.width: 1; border.color: Theme.border
                        anchors.verticalCenter: parent.verticalCenter
                        Label { id: changeLbl; anchors.centerIn: parent; text: "Change"
                                color: Theme.textSecondary; font.pixelSize: Theme.fontMeta }
                        HoverHandler { id: changeHov }
                        TapHandler { onTapped: refDialog.open() }
                    }
                }

                // reference frame: renders the CHOSEN reference image (S10). An Image bound to
                // the picked refPath, fit within the plate; the region overlay + mp4 scrubber
                // ghosts stay. A video ref (or a load failure) shows nothing and the "reference
                // frame" caption remains - honest empty for the still-unbacked video path.
                Rectangle {
                    width: parent.width; height: 150
                    radius: Theme.radiusSm
                    color: Theme.inputWell
                    border.width: 1; border.color: Theme.border
                    clip: true
                    Image {
                        id: refImage
                        objectName: "refImage"
                        anchors.fill: parent
                        anchors.margins: 1
                        source: refTab.refPath
                        fillMode: Image.PreserveAspectFit
                        sourceSize.width: Theme.previewCap
                        asynchronous: true
                        cache: false
                        visible: refTab.refPath !== "" && status === Image.Ready
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: refTab.refPath === ""
                        text: "no reference selected"
                        color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                    }
                    Label {
                        visible: refTab.refPath !== ""
                        anchors.left: parent.left; anchors.bottom: parent.bottom
                        anchors.margins: 8
                        text: "reference frame"
                        color: Theme.textSecondary; font.pixelSize: Theme.fontMicro; font.family: Theme.monoFamily
                    }
                    Rectangle {
                        visible: refTab.refPath !== "" && overlaySwitch.checked
                        x: 120; y: 38; width: 110; height: 60
                        color: "transparent"
                        border.width: 1; border.color: Theme.accent
                        radius: 3
                        objectName: "refRegionOverlay"
                    }
                }

                // mp4 scrubber (anatomy-only; disabled without a decode/scrub backend)
                Row {
                    width: parent.width
                    height: 22
                    spacing: 10
                    opacity: 0.5
                    Rectangle {
                        objectName: "refScrubPlay"
                        enabled: false
                        width: 22; height: 22; radius: 11
                        color: "transparent"
                        border.width: 1; border.color: Theme.borderStrong
                        anchors.verticalCenter: parent.verticalCenter
                        Canvas {
                            anchors.centerIn: parent
                            width: 8; height: 8
                            onPaint: {
                                var ctx = getContext("2d");
                                ctx.fillStyle = Qt.rgba(0.95, 0.95, 0.95, 1);
                                ctx.beginPath(); ctx.moveTo(1, 0); ctx.lineTo(7, 4); ctx.lineTo(1, 8);
                                ctx.closePath(); ctx.fill();
                            }
                        }
                    }
                    Rectangle {
                        width: parent.width - 22 - 90 - 20; height: 3; radius: 1.5
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.border
                        Rectangle { width: 0; height: parent.height; radius: 1.5; color: Theme.accent }
                    }
                    Label {
                        width: 90
                        text: "mp4 ref"
                        color: Theme.textTertiary; font.pixelSize: Theme.fontMicro
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                RowLayout {
                    width: parent.width
                    height: 30
                    Label { text: "Show region overlay"; color: Theme.textPrimary; font.pixelSize: Theme.fontControl
                            Layout.alignment: Qt.AlignVCenter }
                    Item { Layout.fillWidth: true }
                    ThemedSwitch {
                        id: overlaySwitch
                        objectName: "refOverlaySwitch"
                        Layout.alignment: Qt.AlignVCenter
                        pillWidth: 26; pillHeight: 15
                        enabled: refTab.refPath !== ""
                        opacity: refTab.refPath !== "" ? 1 : 0.5
                    }
                }

                Item {
                    width: parent.width
                    height: 26
                    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.border }
                    Label {
                        anchors.left: parent.left; anchors.bottom: parent.bottom
                        width: parent.width
                        text: "Float this window beside the live render for the side-by-side · " +
                              "scrubber holds animated-glow refs"
                        color: Theme.textTertiary; font.pixelSize: Theme.fontMicro
                        elide: Text.ElideRight
                    }
                }
            }
            FileDialog {
                id: refDialog
                title: "Choose reference frame"
                nameFilters: ["Images (*.png *.jpg *.jpeg *.webp)", "Video (*.mp4 *.webm)", "All files (*)"]
                onAccepted: { refTab.refPath = selectedFile.toString(); pal._persist(); }
            }
        }

        Item {
            id: verdictTab
            property var recent: (pal.rev, dev.recentVerdicts(8))
            property var runs: (pal.rev, dev.runHistory())
            property int tailOpen: -1
            Column {
                anchors.fill: parent
                spacing: Theme.spacingXs

                Row {
                    width: parent.width
                    spacing: Theme.spacingSm
                    TextField {
                        id: verdictField
                        width: parent.width - logBtn.width - Theme.spacingSm
                        height: 26
                        topPadding: 0; bottomPadding: 0; leftPadding: 10
                        placeholderText: "One-line verdict for the current run"
                        color: Theme.textPrimary; font.pixelSize: Theme.fontControl
                        background: Rectangle { color: Theme.inputWell; radius: Theme.radiusSm
                            border.width: 1; border.color: parent.activeFocus ? Theme.borderStrong : Theme.border }
                        onAccepted: logBtn.commit()
                    }
                    Rectangle {
                        id: logBtn
                        height: 26; width: logLbl.implicitWidth + Theme.spacingLg
                        radius: Theme.radiusSm
                        color: Theme.accent
                        function commit() {
                            if (verdictField.text.trim() !== "") {
                                dev.logVerdict(verdictField.text);
                                verdictField.text = "";
                            }
                        }
                        Label { id: logLbl; anchors.centerIn: parent; text: "Log"
                                color: Theme.onAccent; font.pixelSize: Theme.fontMeta; font.weight: Theme.weightMedium }
                        TapHandler { onTapped: logBtn.commit() }
                    }
                }

                Timer { id: ctxClock; interval: 20000; running: pal.tab === 4; repeat: true
                        property int tick: 0; onTriggered: tick++ }
                Label {
                    width: parent.width
                    text: {
                        var _ = (pal.rev, ctxClock.tick);
                        var parts = [];
                        parts.push(pal.targetName !== "" ? pal.targetName : "now-playing");
                        var d = new Date();
                        function p2(n) { return (n < 10 ? "0" : "") + n; }
                        parts.push(p2(d.getHours()) + ":" + p2(d.getMinutes()));
                        // isolation: solo (by name if resolvable) + skip count. Resolve the
                        // name from the Isolator tab's ALREADY-LOADED object list (StackLayout
                        // instantiates every tab, so isoTab.objs exists) - a direct
                        // dev.objectList() here would re-run the status subprocess + disk read
                        // inside a text binding on every state change (reviewer finding).
                        var iso = dev.isolationState();
                        var solo = iso.soloObjects || [];
                        if (solo.length === 1) {
                            var nm = solo[0];
                            var objs = isoTab.objs || [];
                            for (var i = 0; i < objs.length; i++)
                                if (String(objs[i].objid) === String(solo[0])) { nm = objs[i].name || solo[0]; break; }
                            parts.push("solo " + nm);
                        } else if (solo.length > 1) {
                            parts.push("solo " + solo.length + " objects");
                        }
                        var nskip = (iso.skipObjects ? iso.skipObjects.length : 0)
                                  + (iso.skipEffects ? iso.skipEffects.length : 0);
                        if (nskip > 0) parts.push("skip " + nskip);
                        var ts = dev.ourToggles(); var off = [];
                        for (var j = 0; j < ts.length; j++)
                            if (!dev.fixOn(ts[j].key)) off.push(ts[j].env);
                        if (off.length > 0) parts.push(off.join(",") + " off");
                        // A/B split state. This used to read ab.compare, a key abState() has
                        // not returned since the single-compare model was replaced by
                        // per-side loadouts - so a live A/B appended the literal
                        // "undefined A/B on". Name both sides from what abState actually has.
                        var ab = dev.abState();
                        if (ab.running)
                            parts.push("A/B live: A = " + ab.sideA + ", B = " + ab.sideB);
                        return "Attaches automatically: " + parts.join(" · ");
                    }
                    color: Theme.textTertiary; font.pixelSize: Theme.fontMicro; wrapMode: Text.WordWrap
                }

                Label { text: "Recent verdicts"; color: Theme.textSecondary
                        font.pixelSize: Theme.fontMeta; font.weight: Theme.weightMedium; topPadding: 6 }
                Column {
                    width: parent.width
                    Repeater {
                        model: verdictTab.recent
                        delegate: Item {
                            required property var modelData
                            width: parent.width
                            height: 26
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hoverWash }
                            Row {
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: Theme.spacingSm
                                Label { width: 34; text: modelData.time; color: Theme.textTertiary
                                        font.pixelSize: Theme.fontMicro; font.family: Theme.monoFamily
                                        elide: Text.ElideRight }
                                Label { width: parent.parent.width - 34 - Theme.spacingSm
                                        text: modelData.text; color: Theme.textMutedBody
                                        font.pixelSize: Theme.fontMeta; elide: Text.ElideRight }
                            }
                        }
                    }
                    Label {
                        visible: verdictTab.recent.length === 0
                        text: "No verdicts logged yet"
                        color: Theme.textTertiary; font.pixelSize: Theme.fontMicro
                    }
                }

                Label { text: "Run history"; color: Theme.textSecondary
                        font.pixelSize: Theme.fontMeta; font.weight: Theme.weightMedium; topPadding: 6 }
                Flickable {
                    width: parent.width
                    height: Math.max(0, parent.height - y)
                    contentHeight: runsCol.height
                    clip: true
                    ScrollBar.vertical: ScrollBar {}
                    Column {
                        id: runsCol
                        width: parent.width
                        Repeater {
                            model: verdictTab.runs
                            delegate: Column {
                                id: runRow
                                required property var modelData
                                required property int index
                                width: runsCol.width
                                property bool tailOpen: verdictTab.tailOpen === index
                                Item {
                                    width: parent.width
                                    height: 26
                                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hoverWash }
                                    Row {
                                        anchors.verticalCenter: parent.verticalCenter
                                        anchors.left: parent.left
                                        spacing: Theme.spacingSm
                                        Label { width: 34; text: runRow.modelData.ts; color: Theme.textTertiary
                                                font.pixelSize: Theme.fontMicro; font.family: Theme.monoFamily
                                                elide: Text.ElideRight }
                                        Label { text: "exit " + runRow.modelData.code
                                                color: runRow.modelData.code !== 0 ? Theme.danger : Theme.textMutedBody
                                                font.pixelSize: Theme.fontMeta }
                                    }
                                    Label {
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: runRow.tailOpen ? "Hide" : "View"
                                        color: Theme.textSecondary; font.pixelSize: Theme.fontMeta
                                        TapHandler { onTapped: verdictTab.tailOpen =
                                            (runRow.tailOpen ? -1 : runRow.index) }
                                    }
                                }
                                Rectangle {
                                    visible: runRow.tailOpen
                                    width: parent.width
                                    height: visible ? Math.min(120, tailText.implicitHeight + Theme.spacingSm * 2) : 0
                                    color: Theme.inputWell
                                    border.width: 1; border.color: Theme.border
                                    radius: Theme.radiusXs
                                    clip: true
                                    Flickable {
                                        anchors.fill: parent
                                        anchors.margins: Theme.spacingSm
                                        contentHeight: tailText.implicitHeight
                                        clip: true
                                        Label {
                                            id: tailText
                                            width: parent.width
                                            text: (pal.rev, dev.runTail(runRow.index)) || "(no output captured)"
                                            color: Theme.textMutedBody
                                            font.pixelSize: Theme.fontMicro
                                            font.family: Theme.monoFamily
                                            wrapMode: Text.WrapAnywhere
                                        }
                                    }
                                }
                            }
                        }
                        Label {
                            visible: verdictTab.runs.length === 0
                            text: "No runs yet"
                            color: Theme.textTertiary; font.pixelSize: Theme.fontMicro
                        }
                    }
                }

                Item {
                    width: parent.width
                    height: 26
                    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.border }
                    Label {
                        anchors.left: parent.left; anchors.bottom: parent.bottom
                        width: parent.width
                        text: "Append-only dev log; a crashed or unsaved session still leaves its trail"
                        color: Theme.textTertiary; font.pixelSize: Theme.fontMicro; elide: Text.ElideRight
                    }
                }
            }
        }
    }

    // S13: 1px strong border outlining the frameless window (radius 8). Drawn
    // last so it frames the whole surface; no input handlers so it never eats a click.
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        radius: 8
        border.width: 1
        border.color: Theme.borderStrong
    }
}
