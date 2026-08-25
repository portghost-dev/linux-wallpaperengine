pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import "."

Item {
    id: root
    objectName: "workshopView"

    signal openEditor(string id)

    property bool steam: false
    property int stateRev: 0

    Connections {
        target: workshop
        function onSteamChanged() { root.steam = workshop.steamAvailable() }
        function onStateChanged() { root.stateRev++; root.refresh() }
    }
    Connections {
        target: importBridge
        function onScanFinished(found, imported) { root.refresh(); root.depSweep() }
        function onBusyChanged() { root.stateRev++ }
    }

    onVisibleChanged: {
        importBridge.setScopeHot(visible);
        if (visible) {
            workshop.recheckSteam();
            root.steam = workshop.steamAvailable();
            root.refresh();
        } else {
            for (var i = wsModel.count - 1; i >= 0; i--)
                if (wsModel.get(i).kept)
                    wsModel.remove(i);
        }
    }

    ListModel { id: wsModel }

    // Order-stable merge: existing rows keep their slot (kept ghosts always survive),
    // vanished rows leave, NEW arrivals insert at the top (newest-first law) so the
    // Flow's add transition is the arrival motion.
    function refresh() {
        var items = workshop.itemList();
        var byId = {};
        for (var i = 0; i < items.length; i++)
            byId[items[i].wid] = items[i];
        for (var r = wsModel.count - 1; r >= 0; r--) {
            var row = wsModel.get(r);
            if (row.kept)
                continue;
            var cur = byId[row.wid];
            if (cur === undefined) {
                wsModel.remove(r);
            } else {
                wsModel.set(r, { wid: cur.wid, title: cur.title,
                                 thumb: String(cur.thumb), wpType: cur.wpType,
                                 forecast: cur.forecast, kept: false, fresh: false,
                                 crashed: cur.crashed === true,
                                 depMissing: cur.depMissing === true,
                                 depWid: String(cur.depWid || ""),
                                 depName: String(cur.depName || "") });
                delete byId[cur.wid];
            }
        }
        var seen = {};
        for (var s = 0; s < wsModel.count; s++)
            seen[wsModel.get(s).wid] = true;
        // walk items in order, inserting unseen ones at the top in reverse so the
        // final order matches itemList (newest first)
        for (var n = items.length - 1; n >= 0; n--) {
            var it = items[n];
            if (seen[it.wid] || byId[it.wid] === undefined)
                continue;
            wsModel.insert(0, { wid: it.wid, title: it.title,
                                thumb: String(it.thumb), wpType: it.wpType,
                                forecast: it.forecast, kept: false,
                                fresh: root.visible,
                                crashed: it.crashed === true,
                                depMissing: it.depMissing === true,
                                depWid: String(it.depWid || ""),
                                depName: String(it.depName || "") });
        }
    }

    // 16e triggers: show the modal for a NEWLY seen held item (once per session);
    // close it the moment its item resolves or leaves
    property var shownDeps: ({})
    function depSweep() {
        var openWid = depDialog.visible ? depDialog.wid : "";
        var openStillHeld = false;
        for (var i = 0; i < wsModel.count; i++) {
            var row = wsModel.get(i);
            if (!row.depMissing)
                continue;
            if (row.wid === openWid)
                openStillHeld = true;
            if (root.visible && !shownDeps[row.wid] && !depDialog.visible
                    && !trashDialog.visible) {
                shownDeps[row.wid] = true;
                depDialog.openFor(row.wid, row.title);
            }
        }
        if (openWid !== "" && !openStillHeld)
            depDialog.close();
    }


    Column {
        id: hero
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.leftMargin: Theme.spacingXl
        anchors.topMargin: Theme.spacingXl
        width: Math.min(760, parent.width - Theme.spacingXl * 2)
        spacing: Theme.spacingMd

        Label {
            text: "Get more wallpapers"
            color: Theme.textPrimary
            font.pixelSize: 20
            font.weight: Theme.weightMedium
        }

        Grid {
            columns: Theme.compact2 ? 1 : 3
            columnSpacing: 22
            rowSpacing: Theme.compact2 ? 8 : 22
            Repeater {
                model: [
                    { n: "1", hot: true, t: "Subscribe on the Steam Workshop",
                      sub: "Click Get wallpapers below to get started. Requires Wallpaper Engine "
                           + "owned and installed on Steam. Steam downloads what you subscribe to." },
                    { n: "2", hot: false, t: "Come back here",
                      sub: "We scan for it automatically and it appears below." },
                    { n: "3", hot: false, t: "We bench it together",
                      sub: "We test for crashes and visual fidelity before importing." }
                ]
                delegate: Column {
                    id: step
                    required property var modelData
                    width: Theme.compact2 ? hero.width : (hero.width - 44) / 3
                    spacing: Theme.spacingXs
                    Row {
                        id: stepHead
                        width: parent.width
                        spacing: Theme.spacingSm
                        Rectangle {
                            width: 18; height: 18; radius: 9
                            anchors.verticalCenter: parent.verticalCenter
                            color: "transparent"
                            border.width: 1
                            border.color: step.modelData.hot ? Theme.accent : Theme.borderStrong
                            Label {
                                anchors.centerIn: parent
                                text: step.modelData.n
                                color: step.modelData.hot ? Theme.accent : Theme.textSecondary
                                font.pixelSize: 10
                                font.weight: Theme.weightMedium
                            }
                        }
                        Label {
                            anchors.verticalCenter: parent.verticalCenter
                            // law 0: the step row elides rather than running into its neighbor
                            width: Math.max(0, stepHead.width - 18 - Theme.spacingSm)
                            elide: Text.ElideRight
                            text: step.modelData.t
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontBody13
                            font.weight: Theme.weightMedium
                        }
                    }
                    Label {
                        width: parent.width
                        visible: !Theme.compact
                        text: step.modelData.sub
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMeta
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }

        Label {
            id: noteLabel
            visible: text !== ""
            color: Theme.warning
            font.pixelSize: Theme.fontMeta
            function show(msg) { text = msg; noteClear.restart() }
            Timer { id: noteClear; interval: 4000; onTriggered: noteLabel.text = "" }
        }
    }
    Rectangle {
        id: heroRule
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: hero.bottom
        anchors.topMargin: Theme.spacingSm
        height: 1
        color: Theme.border
    }

    Flickable {
        id: tileScroll
        objectName: "workshopTileScroll"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: heroRule.bottom
        anchors.bottom: parent.bottom
        anchors.margins: Theme.spacingLg
        anchors.rightMargin: 0
        contentHeight: tileFlow.height + sliverSnap
        clip: true

        // responsive law v1.6 (harmonized with the Library grid, B16): auto-fit tile
        // width in [236,320], 16:10 aspect-locked thumb - IDENTICAL sizing to the library
        // so the one tile component never forks. The optical row-flex is a Library-grid
        // property (this list scrolls under the hero, no clipped-last-row problem), so it
        // stays there; only the base 16:10 sizing is shared.
        readonly property int gap: Theme.spacingLg
        readonly property int minTile: 216
        readonly property int maxTile: 320
        readonly property int cols: Math.max(1, Math.floor(width / (minTile + gap)))
        readonly property int tileW: Math.min(maxTile, Math.floor(width / cols) - gap)
        readonly property int thumbH: Math.round(tileW * 10 / 16)
        readonly property int tileH: thumbH + 34 + 2

        readonly property int pitch: tileH + gap
        readonly property int contentRows: Math.ceil(Math.max(0, wsModel.count) / cols)
        readonly property real contentH: contentRows > 0
                                         ? contentRows * tileH + (contentRows - 1) * gap : 0
        readonly property int restFullRows: Math.max(0, Math.floor((height + gap) / pitch))
        readonly property real restPartial: height
                                           - (restFullRows * tileH + Math.max(0, restFullRows - 1) * gap)
        readonly property real sliverSnap: (contentH > height && restPartial > 0.5 && restPartial < 24)
                                           ? restPartial : 0

        Flow {
            id: tileFlow
            objectName: "workshopFlow"
            width: parent.width
            y: tileScroll.sliverSnap
            spacing: Theme.spacingLg

            add: Transition {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 160 }
                NumberAnimation { property: "scale"; from: 0.97; to: 1; duration: 160 }
            }
            populate: Transition {}

            // grid-removal contract (v2.3.1): the leaving cell fades, the rest reflow. The model
            // drop is synchronous (kills the stale-cell glitch); this is the cosmetic layer on top.
            move: Transition {
                NumberAnimation { properties: "x,y"; duration: Motion.removeReflow; easing.type: Motion.removeReflowEasing }
            }

            AddTile {
                width: tileScroll.tileW
                height: tileScroll.tileH
                heading: root.steam ? "Get wallpapers" : "Get Steam"
                subcopy: root.steam ? "Subscribe on Steam" : "Opens the download page"
                onClicked: workshop.openWorkshop()
                onAdvancedRequested: manualAdd.openModal()
            }

            TombstoneTile {
                visible: (root.stateRev, workshop.bypassableWids().length > 0)
                width: visible ? tileScroll.tileW : 0
                height: visible ? tileScroll.tileH : 0
                count: (root.stateRev, workshop.bypassableWids().length)
                onClicked: tombstoneImport.openModal()
            }

            Rectangle {
                visible: (root.stateRev, importBridge.isBusy() && importBridge.arrivalPending())
                width: tileScroll.tileW; height: tileScroll.tileH
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: Theme.accent
                Column {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingMd
                    spacing: Theme.spacingSm
                    Row {
                        spacing: Theme.spacingSm
                        Rectangle {
                            width: 7; height: 7; radius: 3.5
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.accent
                        }
                        Label {
                            text: "Arriving"
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontMeta
                        }
                    }
                }
                Canvas {
                    // Canvas paints ONCE and reads its Theme color AT PAINT TIME, so a theme
                    // switch changed the binding but left the pixels alone (glyphs kept the old
                    // palette until an app restart). Theme.rev ticks on every theme change.
                    property int themeRev: Theme.rev
                    onThemeRevChanged: requestPaint()
                    anchors.fill: parent
                    anchors.margins: 1
                    opacity: 0.35
                    onPaint: {
                        var c = getContext("2d");
                        c.reset();
                        c.strokeStyle = Theme.surfaceVariant;
                        c.lineWidth = 6;
                        for (var x = -height; x < width + height; x += 18) {
                            c.beginPath();
                            c.moveTo(x, height);
                            c.lineTo(x + height, 0);
                            c.stroke();
                        }
                    }
                }
                Rectangle {
                    anchors.left: parent.left
                    anchors.bottom: parent.bottom
                    anchors.margins: Theme.spacingMd
                    width: 96; height: 8; radius: 4
                    color: Theme.surfaceVariant
                }
            }

            Repeater {
                model: wsModel
                delegate: WorkshopTile {
                    id: tileDelegate
                    required property var model
                    width: tileScroll.tileW
                    height: tileScroll.tileH
                    thumbHeight: tileScroll.thumbH
                    wid: model.wid
                    title: model.title
                    thumb: model.thumb
                    wpType: model.wpType
                    forecast: model.forecast
                    crashed: model.crashed === true
                    depMissing: model.depMissing === true
                    // shovel-pickaxe -> the import wizard (the bench IS the preview)
                    onBenchClicked: function(id) { wizardBridge.open(id, tileDelegate.title); }
                    onEditClicked: function(id) {
                        if (tileDelegate.depMissing)
                            depDialog.openFor(id, tileDelegate.title);
                        else
                            root.openEditor(id);
                    }
                    onTrashRequested: function(id) { trashDialog.openFor(id, tileDelegate.title); }
                    onDepChipClicked: function(id) { depDialog.openFor(id, tileDelegate.title); }
                }
            }
        }


        ScrollBar.vertical: ScrollBar {}
    }


    // ------------------------------------------------- missing-dependency modal (16e)
    // Shown on arrival when the manifest names a dependency we don't have; chip click
    // reopens it; auto-closes when the resolve pass completes the import hands-free.
    Popup {
        id: depDialog
        objectName: "workshopDepDialog"
        property string wid: ""
        property string wpTitle: ""
        property string depWid: ""
        property string depName: ""

        function openFor(id, title) {
            var info = workshop.depInfo(id);
            if (info.missing !== true)
                return;
            wid = id;
            wpTitle = title;
            depWid = String(info.depWid);
            depName = String(info.depName);
            open();
        }

        anchors.centerIn: parent
        width: 400
        height: 302
        modal: true
        focus: true
        padding: 0
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            radius: Theme.radiusLg
            color: Theme.surfaceVariant
            border.width: 1
            border.color: Theme.borderStrong
        }
        Overlay.modal: Rectangle { color: Theme.scrimHover }

        contentItem: Item {
            WizardFace {
                title: "Missing a dependency"
                body: depDialog.wpTitle + " builds on another Workshop item and won't run "
                      + "without it. Get it on Steam and the import finishes automatically "
                      + "when it arrives."
                primaryText: "Get it on Steam"
                primaryColor: Theme.accent
                ghostText: "Trash this scene instead"
                finePrint: "Until resolved it stays in Workshop marked missing dependency"
                onPrimaryClicked: {
                    workshop.openItemPage(depDialog.depWid);
                    depDialog.close();
                }
                onGhostClicked: {
                    depDialog.close();
                    trashDialog.openFor(depDialog.wid, depDialog.wpTitle);
                }
                wellContent: Item {
                    anchors.fill: parent
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        spacing: Theme.spacingSm
                        Rectangle {
                            width: 7; height: 7; radius: 3.5
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.warning
                        }
                        Label {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Depends on " + (depDialog.depName !== ""
                                  ? depDialog.depName : "another Workshop item")
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontControl
                            elide: Text.ElideRight
                        }
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.right: parent.right
                        text: depDialog.depWid
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMicro
                        font.family: Theme.monoFamily
                    }
                }
            }
        }
    }

    TrashWizard {
        id: trashDialog
        objectName: "workshopTrashDialog"
    }

    WizardModal {
        id: wizardModal
        parent: Overlay.overlay
    }
    Connections {
        target: wizardBridge
        function onDepNeeded(wid, title) { depDialog.openFor(wid, title); }
        function onNote(msg) { noteLabel.show(msg); }
        // the wizard graduated this item into the library: drop it from the grid immediately
        // (no green-check step, no lingering "In library" ghost)
        function onGraduated(wid) {
            for (var i = wsModel.count - 1; i >= 0; i--) {
                if (wsModel.get(i).wid === wid) { wsModel.remove(i); break; }
            }
        }
        // wizard deny trashed the item: drop the tile and offer the same unsubscribe beat the
        // tile's own trash button would (one trash protocol)
        function onTrashedUnsub(wid, title) {
            for (var i = wsModel.count - 1; i >= 0; i--) {
                if (wsModel.get(i).wid === wid) { wsModel.remove(i); break; }
            }
            trashDialog.openUnsubscribeOnly(wid, title);
        }
    }

    ManualAddModal {
        id: manualAdd
        parent: Overlay.overlay
        // the copy lands in LWE's pending root; a rescan is what turns it into a Workshop tile
        onAdded: importBridge.rescanNow()
    }

    TombstoneManager {
        id: tombstoneImport
        parent: Overlay.overlay
        // re-scan immediately so imported items appear in the grid now, rather than whenever the
        // folder watcher next fires
        onImportPerformed: importBridge.rescanNow()
    }
}
