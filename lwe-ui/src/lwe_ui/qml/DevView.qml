import QtQuick
import QtQuick.Controls.Basic
import "."

Rectangle {
    id: view

    signal closed()

    color: Theme.base
    // RAIL-BLEED FIX. The bleeding element is the CENTERED TAB STRIP, not
    // the content pane - tabStripBox is anchors.centerIn with an unbounded
    // `width: tabRow.implicitWidth + 2`, so below usable ~676 it starts at a negative x and
    // paints straight over the 64px rail (Main.qml declares Rail before DevView and sets no z).
    // The content pane's own Flickables already clip. Clipping the ROOT catches the strip, the
    // launcher and anything else that ever overhangs, at every width.
    clip: true
    property int rev: 0
    property int tab: 0
    property string targetName: "Now playing"

    // Esc-to-library is handled by Main.qml's single global shortcut. A second Escape
    // Shortcut here would be ambiguous with it (Qt fires NEITHER on ambiguity), which is
    // exactly the dead-Escape bug the conformance pass caught - so this view declares none.

    Connections {
        target: dev
        function onStateChanged() { view.rev++ }
        function onRunsChanged() { view.rev++ }
        function onLogLine(line) {
            // capped + deferred: an uncapped model with a synchronous scroll per line is
            // what let one heavy instrument freeze the whole GUI
            view.appendLog(line, "bench");
        }
        // the daemon's journal shares the console but arrives on its own signal, so it
        // never reaches the per-lens readout below (which splits logLine by scoped tag)
        function onJournalLine(line) { view.appendLog(line, "journal"); }
        function onJournalChanged() { view.rev++ }
    }

    function appendLog(line, src) {
        logModel.append({line: line, source: src});
        if (logModel.count > 500) logModel.remove(0, 100);
        if (logView.atYEnd || logModel.count === 1)
            Qt.callLater(logView.positionViewAtEnd);
    }

    ListModel { id: logModel }

    property int clockTick: 0
    Timer { interval: 1000; running: true; repeat: true; onTriggered: view.clockTick++ }
    function _mmss(secs) {
        var m = Math.floor(secs / 60);
        var s = secs % 60;
        return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
    }

    property var engineStatus: ({})

    // Human forms of the engine leaves. Each returns "" when the daemon has not answered,
    // and the readout hides rather than printing a dash: an absent engine is not a value.
    function engUptime() {
        var s = parseInt(view.engineStatus.uptime_s);
        if (isNaN(s) || s <= 0) return "";
        if (s < 3600) return Math.floor(s / 60) + "m";
        var h = Math.floor(s / 3600);
        return h < 24 ? h + "h" + Math.floor((s % 3600) / 60) + "m"
                      : Math.floor(h / 24) + "d" + (h % 24) + "h";
    }
    function engOutputs() {
        var st = String(view.engineStatus.outputs_state || "");
        if (st === "") return "";
        var why = String(view.engineStatus.outputs_reason || "");
        // "released" without a reason is the one genuinely ambiguous state - say so
        return st === "live" ? "outputs live"
             : "outputs " + st + (why !== "" ? " (" + why + ")" : " (reason unreported)");
    }
    function engPause() {
        if (view.engineStatus.manual_pause === true) return "paused";
        if (view.engineStatus.fullscreen_pause === true) return "paused (fullscreen)";
        return "";
    }
    function engRotation() {
        var n = parseInt(view.engineStatus.rotation_count);
        if (isNaN(n) || n <= 0) return "";
        var pos = parseInt(view.engineStatus.rotation_pos);
        return isNaN(pos) ? n + " in set" : "history " + pos + "/" + n;
    }

    Rectangle {
        id: sessionBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 40
        color: Theme.base
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }

        Row {
            id: targetRow
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: Theme.spacingLg
            spacing: Theme.spacingMd

            Label {
                anchors.verticalCenter: parent.verticalCenter
                text: "Target"
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMeta
            }

            ThemedCombo {
                id: targetCombo
                anchors.verticalCenter: parent.verticalCenter
                minWidth: 150
                width: Math.max(minWidth, Theme.compact ? 150 : 220)
                property var probes: (view.rev, dev.probeList())
                model: {
                    var m = [{name: "Now playing", target: ""}];
                    for (var i = 0; i < probes.length; i++) m.push(probes[i]);
                    return m;
                }
                textRole: "name"
                onActivated: {
                    dev.setTarget(model[currentIndex].target);
                    view.targetName = model[currentIndex].name;
                }
            }

            ThemedCombo {
                id: probeCombo
                anchors.verticalCenter: parent.verticalCenter
                minWidth: 110
                width: Math.max(minWidth, Theme.compact ? 110 : 150)
                property var probes: (view.rev, dev.probeList())
                model: {
                    var m = [{name: "Probe: none", target: ""}];
                    for (var i = 0; i < probes.length; i++)
                        m.push({name: probes[i].name, target: probes[i].target});
                    return m;
                }
                textRole: "name"
                onActivated: {
                    if (currentIndex > 0) {
                        dev.setTarget(model[currentIndex].target);
                        view.targetName = model[currentIndex].name;
                    }
                }
            }
        }

        // Engine readout: the DAEMON's state, not the bench's. Centered so it reads as a
        // property of the session rather than a control, and elided so a long release
        // reason cannot push the transport off the bar.
        Row {
            id: engineReadout
            objectName: "devEngineReadout"
            // anchored into the gap between the two existing rows rather than centered
            // against a guessed width. The first cut used
            // `Math.min(implicitWidth, sessionBar.width - 700)`, which goes NEGATIVE before
            // the bar has been laid out - width -700 plus clip:true renders nothing at all.
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: targetRow.right
            anchors.right: transportRow.left
            anchors.leftMargin: Theme.spacingLg
            anchors.rightMargin: Theme.spacingLg
            spacing: Theme.spacingSm
            // hides when the daemon has not answered, and also when the bar is too narrow
            // to hold it: at the 1080 minimum artboard the target and transport rows can
            // meet, and an anchored gap that has closed computes a NEGATIVE width.
            visible: String(view.engineStatus.state || "") !== "" && width > 40
            clip: true

            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 6; height: 6; radius: 3
                color: (view.engineStatus.state || "") === "up"
                       ? (view.engPause() !== "" ? Theme.warning : Theme.success)
                       : Theme.textTertiary
            }
            Label {
                anchors.verticalCenter: parent.verticalCenter
                text: (view.engineStatus.state || "") === "up" ? "engine" : "engine down"
                color: Theme.textSecondary
                font.pixelSize: Theme.fontMeta
            }
            Repeater {
                model: [view.engPause(), view.engOutputs(), view.engRotation(),
                        view.engUptime() !== "" ? "up " + view.engUptime() : "",
                        view.engineStatus.clients !== undefined
                            ? view.engineStatus.clients + " client"
                              + (view.engineStatus.clients === 1 ? "" : "s") : ""]
                delegate: Row {
                    required property string modelData
                    visible: modelData !== ""
                    spacing: Theme.spacingSm
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "·"
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMeta
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        text: parent.modelData
                        color: parent.modelData.indexOf("paused") === 0 ? Theme.warning
                             : Theme.textTertiary
                        font.pixelSize: Theme.fontMeta
                        font.family: Theme.monoFamily
                        elide: Text.ElideRight
                    }
                }
            }
        }

        Row {
            id: transportRow
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: parent.right
            anchors.rightMargin: Theme.spacingLg
            spacing: Theme.spacingSm

            component BarBtn: Button {
                id: bb
                property bool danger: false
                property bool accent: false
                property bool filled: false
                property bool activeChip: false
                height: 26
                // the auto-relaunch switch's invisible hit floor makes this Row 40 tall;
                // without a cross-axis anchor the 26px buttons top-align against it
                anchors.verticalCenter: parent ? parent.verticalCenter : undefined
                contentItem: Row {
                    spacing: Theme.spacingXs
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        text: bb.text
                        color: bb.accent ? Theme.onAccent : bb.danger ? Theme.danger : Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                    }
                    Rectangle {
                        visible: bb.activeChip
                        anchors.verticalCenter: parent.verticalCenter
                        width: 6; height: 6; radius: 3; color: Theme.accent
                    }
                }
                background: Rectangle {
                    radius: Theme.radiusSm
                    color: bb.accent ? Theme.accent
                         : bb.filled ? Theme.surfaceVariant
                         : bb.activeChip ? Theme.activeWash
                         : bb.hovered ? (bb.danger ? Theme.dangerWash : Theme.hoverWash) : "transparent"
                    border.width: bb.accent ? 0 : 1
                    border.color: bb.danger ? Theme.danger
                                : (bb.filled || bb.activeChip) ? Theme.borderStrong : Theme.border
                }
            }

            Row {
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spacingXs
                ThemedSwitch {
                    anchors.verticalCenter: parent.verticalCenter
                    pillWidth: 24; pillHeight: 14
                    checked: (view.rev, dev.autoRelaunch())
                    onToggled: dev.setAutoRelaunch(checked)
                }
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Auto-relaunch"
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontMeta
                }
            }

            BarBtn {
                accent: !(view.rev, dev.isRunning())
                filled: (view.rev, dev.isRunning())
                text: (view.rev, dev.isRunning()) ? "Stop bench" : "Start bench"
                onClicked: dev.isRunning() ? dev.stopBench() : dev.startBench()
            }

        }
    }
    Item {
        id: tabStrip
        anchors.top: sessionBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: 44

        Rectangle {
            id: launcher
            anchors.left: parent.left
            anchors.leftMargin: Theme.spacingLg
            anchors.verticalCenter: parent.verticalCenter
            height: 26
            width: Theme.compact ? 30 : launchLabel.implicitWidth + 26
            radius: Theme.radiusSm
            color: Theme.warning
            Label {
                id: launchLabel
                anchors.centerIn: parent
                visible: !Theme.compact
                text: "Launch developer tooling"
                color: Theme.onAccent   // dark ink; amber and accent both take the same on-ink
                font.pixelSize: Theme.fontControl
                font.weight: Theme.weightMedium
            }
            // bug glyph, drawn rather than imported - there is no bug in the icon set and this
            // is its only consumer. Same on-ink as the label it replaces.
            Canvas {
                anchors.centerIn: parent
                width: 16; height: 16
                visible: Theme.compact
                property int themeRev: Theme.rev
                onThemeRevChanged: requestPaint()
                onPaint: {
                    var c = getContext("2d");
                    c.reset();
                    c.strokeStyle = Theme.onAccent;
                    c.fillStyle = Theme.onAccent;
                    c.lineWidth = 1.3;
                    c.lineCap = "round";
                    c.beginPath();
                    c.ellipse(5, 4.5, 6, 9);
                    c.fill();
                    c.beginPath();
                    c.moveTo(6.2, 4.6); c.lineTo(4.2, 1.8);
                    c.moveTo(9.8, 4.6); c.lineTo(11.8, 1.8);
                    for (var i = 0; i < 3; i++) {
                        var y = 6.2 + i * 2.6;
                        c.moveTo(5, y);  c.lineTo(1.8, y - 1.2);
                        c.moveTo(11, y); c.lineTo(14.2, y - 1.2);
                    }
                    c.stroke();
                }
            }
            TapHandler { onTapped: toolsLoader.toggle() }
        }

        // centered joined tab strip. Terse cell copy + a trailing
        // More + down-chevron cell that opens a menu of any tabs the strip does not show.
        // Built inline to the frame rather than via SegmentControl because the shared control's
        // API is frozen (six surfaces build against it) and has no trailing overflow cell -
        // this is a faithful reproduction of the segment grammar (h28: border, radius 6, 1px
        // separators, segmentWash active cell), not a fork.
        Rectangle {
            id: tabStripBox
            anchors.centerIn: parent
            height: 28
            // BOUNDED SIBLING. This was `tabRow.implicitWidth + 2` with no
            // ceiling, so a centered 676px strip overlapped the amber launcher by 81px at usable
            // 896 - before any compact rule fired - and ran off the left edge onto the rail
            // below ~676. The cap is symmetric because the box is centered: whatever gutter the
            // launcher needs on the left has to be reserved on the right too, or centring alone
            // pushes the strip back over it.
            readonly property real _gutter: launcher.x + launcher.width + Theme.spacingMd
            readonly property real _maxWidth: Math.max(120, parent.width - 2 * _gutter)
            width: Math.min(tabRow.implicitWidth + 2, _maxWidth)
            radius: Theme.radiusSm
            color: "transparent"
            border.width: 1
            border.color: Theme.border
            clip: true

            // terse cell labels, index-aligned to dev.subsystems(); only "Lighting & Models"
            // shortens to "Lighting" (canvas cell). The rest match the subsystem names.
            readonly property var _fullNames: (view.rev, dev.subsystems())
            readonly property var _terse: ["Tour", "Lighting", "Particles", "Puppets", "Bloom",
                                           "Audio", "Camera", "Performance", "Render"]
            FontMetrics { id: cellMetrics; font.pixelSize: 12 }

            function _cellWidth(name) {
                return cellMetrics.advanceWidth(name || "") + 24;
            }
            function _fitCount(avail) {
                var names = tabStripBox._fullNames;
                if (!names || names.length === 0)
                    return 0;
                var moreW = tabStripBox._cellWidth("More") + 5 + 12;
                var total = 0;
                for (var i = 0; i < names.length; i++) {
                    var w = tabStripBox._cellWidth(tabStripBox._terse[i] || names[i]);
                    var reserve = (i < names.length - 1) ? moreW : 0;
                    if (total + w + reserve > avail)
                        return i;
                    total += w;
                }
                return names.length;
            }
            readonly property int _shown: Math.max(1, _fitCount(_maxWidth - 2))

            Row {
                id: tabRow
                anchors.fill: parent
                anchors.margins: 1

                Repeater {
                    model: Math.min(tabStripBox._shown, tabStripBox._fullNames.length)
                    delegate: Item {
                        id: tabCell
                        required property int index
                        readonly property bool current: view.tab === index
                        width: tabLbl.implicitWidth + 24
                        height: tabRow.height
                        Rectangle {
                            visible: tabCell.index > 0
                            width: 1; height: parent.height
                            anchors.left: parent.left
                            color: Theme.border
                        }
                        Rectangle {
                            anchors.fill: parent
                            color: tabCell.current ? Theme.segmentWash : "transparent"
                        }
                        Label {
                            id: tabLbl
                            anchors.centerIn: parent
                            text: tabStripBox._terse[tabCell.index] || tabStripBox._fullNames[tabCell.index]
                            font.pixelSize: 12
                            color: tabCell.current ? Theme.textPrimary : Theme.textSecondary
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: view.tab = tabCell.index
                        }
                    }
                }

                Item {
                    id: moreCell
                    width: moreRow.implicitWidth + 24
                    height: tabRow.height
                    Rectangle { width: 1; height: parent.height; anchors.left: parent.left; color: Theme.border }
                    Rectangle { anchors.fill: parent; color: moreHov.hovered ? Theme.hoverWash : "transparent" }
                    Row {
                        id: moreRow
                        anchors.centerIn: parent
                        spacing: 5
                        Label { text: "More"; color: Theme.textSecondary; font.pixelSize: 12
                                anchors.verticalCenter: parent.verticalCenter }
                        IconChevron {
                            direction: "down"; size: 12; color: Theme.textSecondary
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    HoverHandler { id: moreHov }
                    // press-outside close races the reopening tap (same fix as the
                    // playlist-strip menus): gate the reopen on the justClosed window
                    TapHandler {
                        onTapped: {
                            if (moreMenu.visible) moreMenu.close();
                            else if (!moreMenu.justClosed) moreMenu.open();
                        }
                    }

                    Menu {
                        id: moreMenu
                        y: moreCell.height
                        property bool justClosed: false
                        onClosed: { justClosed = true; moreGuard.restart() }
                        Timer { id: moreGuard; interval: 150
                                onTriggered: moreMenu.justClosed = false }
                        background: Rectangle {
                            implicitWidth: 150
                            color: Theme.surfaceVariant
                            radius: Theme.radiusMd
                            border.width: 1
                            border.color: Theme.borderStrong
                        }
                        Repeater {
                            model: Math.max(0, tabStripBox._fullNames.length - tabStripBox._shown)
                            delegate: ThemedMenuItem {
                                required property int index
                                readonly property int tabIndex: tabStripBox._shown + index
                                text: tabStripBox._fullNames[tabIndex]
                                onTriggered: view.tab = tabIndex
                            }
                        }
                        ThemedMenuItem {
                            enabled: false
                            visible: tabStripBox._fullNames.length <= tabStripBox._shown
                            height: visible ? implicitHeight : 0
                            text: "All tabs shown"
                        }
                    }
                }
            }
        }
    }

    // --- content area (Tour or a subsystem lens) + instrument dock -----------------------
    //
    // COCKPIT FLOOR: the cockpit does NOT column-shed - dense instrument
    // tables that drop columns lie about what was measured. Instead the content floors at 960
    // and scrolls horizontally below that, with an always-visible 3px bar so the hidden extent
    // is announced rather than discovered. The lens layout is built on `parent.width - 452`
    // arithmetic, so a floored viewport is also what keeps that subtraction meaningful.
    Flickable {
        id: contentScroll
        anchors.top: tabStrip.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: dock.top
        clip: true
        contentWidth: Math.max(width, 960)
        contentHeight: height
        flickableDirection: Flickable.HorizontalFlick
        boundsBehavior: Flickable.StopAtBounds

        ScrollBar.horizontal: ScrollBar {
            policy: contentScroll.contentWidth > contentScroll.width ? ScrollBar.AlwaysOn
                                                                     : ScrollBar.AlwaysOff
            height: 3
            contentItem: Rectangle { radius: 1.5; color: Theme.hairlineStrong }
        }

        Item {
            id: contentPane
            width: contentScroll.contentWidth
            height: contentScroll.contentHeight

            Loader {
                anchors.fill: parent
                active: view.tab === 0
                sourceComponent: tourComp
            }
            Loader {
                anchors.fill: parent
                active: view.tab > 0
                sourceComponent: view.subsystems()[view.tab] === "Render" ? renderComp : subsystemComp
            }
        }
    }

    function subsystems() { return dev.subsystems(); }

    Component {
        id: tourComp
        Item {
            // The tour is a CENTERED column capped at 980px (margin auto), not a
            // full-bleed table - rows were stretching to the window edge and the switch slot
            // reserved 34px for a control whose layout width is 64 (the invisible hit-target
            // padding), overflowing the row and clipping the pill off-screen.
            Column {
                id: tourWrap
                width: Math.min(parent.width - Theme.spacingXl * 2, 980)
                anchors.top: parent.top
                anchors.topMargin: Theme.spacingMd
                anchors.bottom: parent.bottom
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Theme.spacingXs
                Row {
                    spacing: Theme.spacingSm
                    Label { text: "Parity tour"; color: Theme.textPrimary
                            font.pixelSize: Theme.fontBody13; font.weight: Theme.weightMedium }
                    Label {
                        anchors.baseline: parent.children[0].baseline
                        text: (view.rev, dev.ourToggles().length) + " additions vs upstream · " +
                              "flipping a toggle relaunches the engine with that fix off"
                        color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                    }
                }
                Flickable {
                    width: parent.width; height: parent.height - y - Theme.spacingSm
                    contentHeight: tourCol.height; clip: true
                    ScrollBar.vertical: ScrollBar {}
                    Column {
                        id: tourCol
                        width: parent.width
                        Repeater {
                            model: (view.rev, dev.ourToggles())
                            delegate: Item {
                                id: tourRow
                                required property var modelData
                                width: tourCol.width
                                height: 38
                                Rectangle { anchors.bottom: parent.bottom; width: parent.width
                                            height: 1; color: Theme.hoverWash }

                                readonly property int colGap: 14

                                // switch slot: 30px VISIBLE; the ThemedSwitch centers its larger
                                // hit area over it (clickable padding overflows invisibly)
                                Item {
                                    id: swSlot
                                    width: 30; height: parent.height
                                    anchors.right: parent.right
                                    ThemedSwitch {
                                        anchors.centerIn: parent
                                        checked: (view.rev, dev.fixOn(tourRow.modelData.key))
                                        onToggled: dev.setFixOn(tourRow.modelData.key, checked)
                                    }
                                }
                                Label {
                                    id: evCol
                                    width: 96
                                    anchors.right: swSlot.left
                                    anchors.rightMargin: tourRow.colGap
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: tourRow.modelData.evidence !== "" ? tourRow.modelData.evidence : "-"
                                    color: Theme.textSecondary; font.pixelSize: Theme.fontMeta
                                    elide: Text.ElideRight
                                }
                                Label {
                                    id: commitCol
                                    width: 64
                                    anchors.right: evCol.left
                                    anchors.rightMargin: tourRow.colGap
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: tourRow.modelData.commit !== "" ? tourRow.modelData.commit : "-"
                                    color: Theme.textSecondary; font.pixelSize: Theme.fontMicro
                                    font.family: Theme.monoFamily
                                    elide: Text.ElideRight
                                }
                                Label {
                                    id: sysCol
                                    width: 76
                                    anchors.right: commitCol.left
                                    anchors.rightMargin: tourRow.colGap
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: tourRow.modelData.sys
                                    color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                                    elide: Text.ElideRight
                                }
                                Row {
                                    anchors.left: parent.left
                                    anchors.right: sysCol.left
                                    anchors.rightMargin: tourRow.colGap
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: Theme.spacingSm
                                    Label {
                                        text: tourRow.modelData.what
                                        color: Theme.textPrimary
                                        font.pixelSize: Theme.fontBody13
                                        anchors.verticalCenter: parent.verticalCenter
                                        elide: Text.ElideRight
                                    }
                                    Label {
                                        text: tourRow.modelData.env
                                        color: Theme.textTertiary
                                        font.pixelSize: Theme.fontMicro
                                        font.family: Theme.monoFamily
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    Row {
                                        visible: tourRow.modelData.experimental === true
                                        spacing: 5
                                        anchors.verticalCenter: parent.verticalCenter
                                        Rectangle { width: 6; height: 6; radius: 3; color: Theme.warning
                                                    anchors.verticalCenter: parent.verticalCenter }
                                        Label { text: "experimental"; color: Theme.textTertiary
                                                font.pixelSize: Theme.fontMeta
                                                anchors.verticalCenter: parent.verticalCenter }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Component {
        id: subsystemComp
        // G1: the 24px content padding belongs to the CONTAINER (outer
        // padding:12 24), not the instruments column - so the divider lands at 24 + 452 = 476.
        Item {
            id: lens
            property string subName: view.subsystems()[view.tab]
            Row {
                anchors.fill: parent
                anchors.topMargin: Theme.spacingMd
                anchors.leftMargin: Theme.spacingXl
                anchors.rightMargin: Theme.spacingXl
            Item {
                width: 452
                height: parent.height
                Flickable {
                    anchors.fill: parent
                    anchors.rightMargin: 28
                    contentHeight: instCol.height; clip: true
                    ScrollBar.vertical: ScrollBar {}
                    Column {
                        id: instCol
                        width: parent.width
                        spacing: 0

                        Label { text: "Instruments"; color: Theme.textSecondary
                                font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                                bottomPadding: Theme.spacingXs }
                        Repeater {
                            model: (view.rev, dev.scopedInstruments(lens.subName))
                            delegate: DevInstrumentRow {
                                required property var modelData
                                width: instCol.width
                                label: modelData.what
                                env: modelData.env
                                checked: (view.rev, dev.instrumentOn(modelData.env))
                                onToggledOn: function(on) { dev.setInstrument(modelData.env, on); }
                            }
                        }
                        Label {
                            visible: (view.rev, dev.scopedInstruments(lens.subName).length) === 0
                            text: "No census-verified instruments scoped to this subsystem yet"
                            color: Theme.textTertiary; font.pixelSize: Theme.fontMicro
                        }

                        Label { text: "A/B fixes (ours)"; color: Theme.textSecondary
                                font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                                topPadding: Theme.spacingMd; bottomPadding: Theme.spacingXs }
                        Repeater {
                            model: {
                                var out = [];
                                var all = dev.ourToggles();
                                for (var i = 0; i < all.length; i++)
                                    if (all[i].sys === lens.subName) out.push(all[i]);
                                return out;
                            }
                            delegate: DevInstrumentRow {
                                required property var modelData
                                width: instCol.width
                                label: modelData.what
                                env: modelData.env
                                checked: (view.rev, dev.fixOn(modelData.key))
                                onToggledOn: function(on) { dev.setFixOn(modelData.key, on); }
                            }
                        }
                        Label {
                            visible: {
                                var all = dev.ourToggles(); var n = 0;
                                for (var i = 0; i < all.length; i++)
                                    if (all[i].sys === lens.subName) n++;
                                return n === 0;
                            }
                            text: "No shipped fixes scoped to this subsystem yet"
                            color: Theme.textTertiary; font.pixelSize: Theme.fontMicro
                        }

                        Label { text: "Calibration"; color: Theme.textSecondary
                                font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                                topPadding: Theme.spacingMd; bottomPadding: Theme.spacingXs }
                        Row {
                            width: parent.width
                            spacing: Theme.spacingSm
                            ThemedCombo {
                                id: calCombo
                                width: 180
                                property var scenes: (view.rev, dev.probeList())
                                model: {
                                    var m = [];
                                    if (scenes.length === 0) m.push({name: "no cal scenes", target: ""});
                                    for (var i = 0; i < scenes.length; i++) m.push(scenes[i]);
                                    return m;
                                }
                                textRole: "name"
                            }
                            Rectangle {
                                height: 26; width: loadLbl.implicitWidth + Theme.spacingLg
                                radius: Theme.radiusSm
                                color: Theme.accent
                                opacity: calCombo.scenes.length > 0 ? 1 : 0.4
                                Label { id: loadLbl; anchors.centerIn: parent; text: "Load scene"
                                        color: Theme.onAccent; font.pixelSize: Theme.fontControl
                                        font.weight: Theme.weightMedium }
                                TapHandler {
                                    enabled: calCombo.scenes.length > 0
                                    onTapped: {
                                        var s = calCombo.model[calCombo.currentIndex];
                                        if (s && s.target) {
                                            dev.setTarget(s.target);
                                            view.targetName = s.name;
                                            if (dev.isRunning()) dev.stopBench();
                                            dev.startBench();
                                        }
                                    }
                                }
                            }
                            Row {
                                spacing: Theme.spacingXs
                                anchors.verticalCenter: parent.verticalCenter
                                ThemedSwitch {
                                    anchors.verticalCenter: parent.verticalCenter
                                    pillWidth: 24; pillHeight: 14
                                    checked: (view.rev, dev.instrumentOn("LWE_FBPROFILE"))
                                    onToggled: dev.setInstrument("LWE_FBPROFILE", checked)
                                }
                                Label { anchors.verticalCenter: parent.verticalCenter; text: "Log probes"
                                        color: Theme.textSecondary; font.pixelSize: Theme.fontMeta }
                            }
                        }
                    }
                }
            }
            Rectangle { width: 1; height: parent.height; color: Theme.border }
            Item {
                width: Math.max(0, parent.width - 452 - 1)
                height: parent.height
                Column {
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: Math.max(0, Math.min(960, parent.width - 28 - 24))
                    spacing: 0
                    Row {
                        width: parent.width
                        height: 24
                        Label {
                            text: lens.subName + " readout"
                            color: Theme.textSecondary; font.pixelSize: Theme.fontControl
                            font.weight: Theme.weightMedium
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Item { width: parent.width - x - objectsDoor.width; height: 1 }
                        Rectangle {
                            id: objectsDoor
                            height: 24; width: objLbl.implicitWidth + Theme.spacingMd + 10
                            radius: Theme.radiusSm
                            color: objHov.hovered ? Theme.hoverWash : "transparent"
                            border.width: 1; border.color: Theme.border
                            anchors.verticalCenter: parent.verticalCenter
                            Row {
                                anchors.centerIn: parent
                                spacing: Theme.spacingXs
                                Label { id: objLbl; text: "Objects"; color: Theme.textSecondary
                                        font.pixelSize: Theme.fontMeta; anchors.verticalCenter: parent.verticalCenter }
                                IconChevron { direction: "down"; size: 12; color: Theme.textSecondary
                                        anchors.verticalCenter: parent.verticalCenter }
                            }
                            HoverHandler { id: objHov }
                            TapHandler { onTapped: toolsLoader.openTab(0) }
                        }
                    }
                    // column header. The separator must live OUTSIDE the Row: a Row is a
                    // positioner, so a full-width first child shoves every label one full
                    // width to the right ("name" rendering at the far edge, the rest clipped).
                    Item {
                        width: parent.width
                        height: 22
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
                        Row {
                            anchors.fill: parent
                            Label { width: 110; text: "tag"; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                                    anchors.verticalCenter: parent.verticalCenter }
                            Label { width: 60; text: "source"; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                                    anchors.verticalCenter: parent.verticalCenter }
                            Label { width: Math.max(0, parent.width - 110 - 60 - 64); text: "payload"; color: Theme.textTertiary
                                    font.pixelSize: Theme.fontMeta; anchors.verticalCenter: parent.verticalCenter }
                            Label { width: 64; text: ""; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                                    horizontalAlignment: Text.AlignRight; anchors.verticalCenter: parent.verticalCenter }
                        }
                    }
                    // live readout: the bench log stream filtered to THIS lens's instrument
                    // tags (dev.scopedReadoutTags), newest at the bottom, capped at 250 rows.
                    // The empty state stays until a matching line actually arrives.
                    Item {
                        id: readoutPane
                        width: parent.width
                        height: parent.height - y

                        property var tagList: dev.scopedReadoutTags(lens.subName)
                        // the lens component is REUSED across tab switches - without this,
                        // one subsystem's rows linger under the next subsystem's header
                        onTagListChanged: readoutModel.clear()
                        ListModel { id: readoutModel }
                        Connections {
                            target: dev
                            function onLogLine(line) {
                                var tags = readoutPane.tagList;
                                for (var i = 0; i < tags.length; i++) {
                                    var at = line.indexOf(tags[i]);
                                    if (at < 0) continue;
                                    readoutModel.append({
                                        tag: tags[i],
                                        payload: line.substring(at + tags[i].length).trim()
                                    });
                                    if (readoutModel.count > 250) readoutModel.remove(0);
                                    if (readoutList.atYEnd || readoutModel.count === 1)
                                        Qt.callLater(readoutList.positionViewAtEnd);
                                    return;
                                }
                            }
                        }
                        ListView {
                            id: readoutList
                            objectName: "lensReadoutList"
                            anchors.fill: parent
                            clip: true
                            model: readoutModel
                            ScrollBar.vertical: ScrollBar {}
                            delegate: Row {
                                required property var model
                                width: readoutList.width - 12
                                height: 20
                                Label { width: 110; text: model.tag
                                        color: Theme.textSecondary; font.pixelSize: Theme.fontMicro
                                        font.family: Theme.monoFamily; elide: Text.ElideRight
                                        anchors.verticalCenter: parent.verticalCenter }
                                Label { width: 60; text: "log"
                                        color: Theme.textTertiary; font.pixelSize: Theme.fontMicro
                                        anchors.verticalCenter: parent.verticalCenter }
                                Label { width: Math.max(0, parent.width - 110 - 60)
                                        text: model.payload
                                        color: Theme.textMutedBody; font.pixelSize: Theme.fontMicro
                                        font.family: Theme.monoFamily; elide: Text.ElideRight
                                        anchors.verticalCenter: parent.verticalCenter }
                            }
                        }
                        Label {
                            visible: readoutModel.count === 0
                            anchors.centerIn: parent
                            width: parent.width - Theme.spacingXl
                            horizontalAlignment: Text.AlignHCenter
                            text: {
                                if (readoutPane.tagList.length === 0)
                                    return "No census-verified instruments scoped to this subsystem yet";
                                var holding = (view.rev, dev.isHolding());
                                if (!holding)
                                    return "Start a bench run to stream " + lens.subName + " readouts here";
                                if (dev.lensAlwaysOn(lens.subName))
                                    return "No " + lens.subName + " output from this scene - "
                                         + "this lens needs no switch, so an empty table means "
                                         + "the scene has no puppet objects";
                                // one of this lens's instruments IS on but nothing matched:
                                // that is a fact about the scene, not a broken pipe (e.g. the
                                // light trace only prints for scenes with 3D model objects)
                                var insts = dev.scopedInstruments(lens.subName);
                                var anyOn = false;
                                for (var i = 0; i < insts.length; i++)
                                    if (dev.instrumentOn(insts[i].env)) { anyOn = true; break; }
                                if (anyOn)
                                    return "No " + lens.subName + " output from this scene yet - " +
                                           "not every scene exercises these instruments " +
                                           "(the light trace needs 3D model objects; try a probe scene)";
                                // a lens with an always-on channel has nothing to switch, so
                                // "switch an instrument on" would be wrong advice
                                if (dev.lensAlwaysOn(lens.subName))
                                    return lens.subName + " output is not gated by a switch - "
                                         + "it streams whenever the engine has something to say "
                                         + "(a per-load census, and any parse or animation failure)";
                                return "Waiting for " + lens.subName + " probe output - switch an instrument on";
                            }
                            color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
            }
        }
    }

    Component {
        id: renderComp
        // G1: 24px content padding on the CONTAINER; the 452 column keeps only its 28px
        // internal right pad before the divider (divider at 24 + 452 = 476).
        Item {
            Row {
            anchors.fill: parent
            anchors.topMargin: Theme.spacingMd
            anchors.leftMargin: Theme.spacingXl
            anchors.rightMargin: Theme.spacingXl
            Item {
                width: 452
                height: parent.height
                Flickable {
                    anchors.fill: parent
                    anchors.rightMargin: 28
                    contentHeight: rInstCol.height; clip: true
                    ScrollBar.vertical: ScrollBar {}
                    Column {
                        id: rInstCol
                        width: parent.width
                        spacing: 0

                        Label { text: "Dump instruments"; color: Theme.textSecondary
                                font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                                bottomPadding: Theme.spacingXs }
                        Repeater {
                            model: (view.rev, dev.scopedInstruments("Render"))
                            delegate: DevInstrumentRow {
                                required property var modelData
                                width: rInstCol.width
                                label: modelData.what
                                env: modelData.env
                                checked: (view.rev, dev.instrumentOn(modelData.env))
                                onToggledOn: function(on) { dev.setInstrument(modelData.env, on); }
                            }
                        }

                        // render-debug flag group (--render-debug base-only/no-solid-final/pass-log)
                        Row {
                            topPadding: Theme.spacingMd; bottomPadding: Theme.spacingXs
                            spacing: Theme.spacingSm
                            Label { text: "Render debug"; color: Theme.textSecondary
                                    font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                                    anchors.verticalCenter: parent.verticalCenter }
                            Label { text: "--render-debug"; color: Theme.textTertiary
                                    font.pixelSize: Theme.fontMicro; font.family: Theme.monoFamily
                                    anchors.verticalCenter: parent.verticalCenter }
                        }
                        Repeater {
                            model: (view.rev, dev.renderDebugFlags())
                            delegate: DevInstrumentRow {
                                required property var modelData
                                width: rInstCol.width
                                label: modelData.what
                                env: ""
                                checked: (view.rev, dev.renderDebugOn(modelData.key))
                                onToggledOn: function(on) { dev.setRenderDebug(modelData.key, on); }
                            }
                        }
                        Label {
                            text: "Each flip relaunches (debounced) so the pass shows on the display."
                            color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                            topPadding: Theme.spacingSm; wrapMode: Text.WordWrap
                            width: parent.width
                        }
                    }
                }
            }
            Rectangle { width: 1; height: parent.height; color: Theme.border }
            Item {
                width: Math.max(0, parent.width - 452 - 1)
                height: parent.height
                Flickable {
                    anchors.fill: parent
                    anchors.leftMargin: 28
                    contentHeight: rawCol.height; clip: true
                    ScrollBar.vertical: ScrollBar {}
                    Column {
                        id: rawCol
                        width: parent.width
                        spacing: Theme.spacingXs

                        Row {
                            spacing: Theme.spacingXs
                            Label { text: "Raw environment"; color: Theme.textSecondary
                                    font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                                    anchors.verticalCenter: parent.verticalCenter }
                            Label { text: "- applies on next relaunch"; color: Theme.textTertiary
                                    font.pixelSize: Theme.fontMeta; anchors.verticalCenter: parent.verticalCenter }
                        }

                        // D2: editable KEY=VALUE launch-env block. One raw
                        // env line per row; a valid shell-identifier key queues, applying on the
                        // next relaunch. Apply parses the whole block and replaces the queue, so
                        // deleting a line here removes it. Seeded from the queued state on load.
                        Rectangle {
                            width: parent.width
                            height: Math.max(80, envEditor.implicitHeight + Theme.spacingSm * 2)
                            radius: Theme.radiusSm
                            color: Theme.inputWell
                            border.width: 1
                            border.color: envEditor.activeFocus ? Theme.borderStrong : Theme.border
                            TextArea {
                                id: envEditor
                                objectName: "devEnvEditor"
                                anchors.fill: parent
                                anchors.margins: Theme.spacingSm
                                placeholderText: "LWE_FBOPOOL=2\nLWE_POOL_HWM=768"
                                color: Theme.textMutedBody
                                font.pixelSize: Theme.fontMicro
                                font.family: Theme.monoFamily
                                wrapMode: TextEdit.NoWrap
                                selectByMouse: true
                                background: null
                                function reseed() {
                                    var lines = dev.envLines();
                                    var out = [];
                                    for (var i = 0; i < lines.length; i++)
                                        out.push(lines[i].key + "=" + lines[i].value);
                                    text = out.join("\n");
                                }
                                // parse the whole block into the queue (replace semantics), and
                                // report any line whose key is not a valid shell identifier.
                                function applyBlock() {
                                    dev.clearEnvLines();
                                    var bad = [];
                                    var rows = text.split("\n");
                                    for (var i = 0; i < rows.length; i++) {
                                        var r = rows[i].trim();
                                        if (r === "") continue;
                                        var eq = r.indexOf("=");
                                        var key = eq > 0 ? r.slice(0, eq).trim() : r;
                                        var val = eq > 0 ? r.slice(eq + 1) : "";
                                        if (!dev.setEnvLine(key, val)) bad.push(r);
                                    }
                                    envRawError.bad = bad;
                                    reseed();
                                    // "Apply" has to actually apply. The env-line setters
                                    // deliberately do not each schedule a relaunch (a block
                                    // edit would fire one per line), so the button asks for
                                    // it once, here - otherwise pressing Apply looked like a
                                    // no-op while every other control in the tab took effect.
                                    dev.applyPending();
                                }
                                Component.onCompleted: reseed()
                            }
                        }
                        Row {
                            width: parent.width
                            spacing: Theme.spacingSm
                            Rectangle {
                                id: envApply
                                height: 24; width: envApplyLbl.implicitWidth + Theme.spacingLg
                                radius: Theme.radiusSm
                                color: envApplyHov.hovered ? Theme.hoverWash : Theme.surfaceVariant
                                border.width: 1; border.color: Theme.border
                                Label { id: envApplyLbl; anchors.centerIn: parent; text: "Apply"
                                        color: Theme.textPrimary; font.pixelSize: Theme.fontControl }
                                HoverHandler { id: envApplyHov }
                                TapHandler { onTapped: envEditor.applyBlock() }
                            }
                            Rectangle {
                                id: envClear
                                height: 24; width: envClearLbl.implicitWidth + Theme.spacingLg
                                radius: Theme.radiusSm
                                color: envClearHov.hovered ? Theme.hoverWash : "transparent"
                                border.width: 1; border.color: Theme.border
                                Label { id: envClearLbl; anchors.centerIn: parent; text: "Clear"
                                        color: Theme.textSecondary; font.pixelSize: Theme.fontControl }
                                HoverHandler { id: envClearHov }
                                TapHandler { onTapped: { dev.clearEnvLines(); envEditor.text = "";
                                                        envRawError.bad = []; } }
                            }
                            Label {
                                anchors.verticalCenter: parent.verticalCenter
                                text: "Apply relaunches the bench with these"
                                color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                            }
                        }
                        Label {
                            id: envRawError
                            property var bad: []
                            visible: bad.length > 0
                            width: parent.width
                            text: bad.length > 0
                                  ? ("Ignored - key not a shell identifier: " + bad.join(", "))
                                  : ""
                            color: Theme.danger; font.pixelSize: Theme.fontMeta; wrapMode: Text.WordWrap
                        }

                        Row {
                            width: parent.width
                            spacing: Theme.spacingSm
                            topPadding: Theme.spacingSm
                            Label { text: "Set property"; color: Theme.textSecondary
                                    font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                                    width: 96; anchors.verticalCenter: parent.verticalCenter }
                            TextField {
                                id: propField
                                width: Math.max(0, parent.width - 96 - setBtn.width - Theme.spacingSm * 2)
                                height: 26
                                topPadding: 0; bottomPadding: 0; leftPadding: 10
                                placeholderText: "name=value"
                                color: Theme.textMutedBody; font.pixelSize: Theme.fontMicro
                                font.family: Theme.monoFamily
                                background: Rectangle { color: Theme.inputWell; radius: Theme.radiusSm
                                    border.width: 1; border.color: parent.activeFocus ? Theme.borderStrong : Theme.border }
                                function commit() {
                                    var t = text.trim();
                                    var eq = t.indexOf("=");
                                    if (eq > 0) { dev.queueSetProperty(t.slice(0, eq), t.slice(eq + 1)); text = ""; }
                                }
                                onAccepted: commit()
                            }
                            Rectangle {
                                id: setBtn
                                height: 26; width: setLbl.implicitWidth + Theme.spacingLg
                                anchors.verticalCenter: parent.verticalCenter
                                radius: Theme.radiusSm
                                color: setHov.hovered ? Theme.hoverWash : Theme.surfaceVariant
                                border.width: 1; border.color: Theme.border
                                Label { id: setLbl; anchors.centerIn: parent; text: "Set"
                                        color: Theme.textPrimary; font.pixelSize: Theme.fontControl }
                                HoverHandler { id: setHov }
                                TapHandler { onTapped: propField.commit() }
                            }
                        }
                        Label {
                            width: parent.width
                            leftPadding: 104
                            text: "Raw --set-property, no validation, developer's honor · " +
                                  "queued, applies on next relaunch"
                            color: Theme.textTertiary; font.pixelSize: Theme.fontMeta; wrapMode: Text.WordWrap
                        }
                        Repeater {
                            model: (view.rev, dev.setProperties())
                            delegate: Row {
                                required property var modelData
                                width: parent.width
                                leftPadding: 104
                                spacing: Theme.spacingSm
                                Label { text: modelData.name + "=" + modelData.value
                                        color: Theme.textMutedBody; font.pixelSize: Theme.fontMicro
                                        font.family: Theme.monoFamily }
                                Label { text: "x"; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                                        TapHandler { onTapped: dev.clearProperty(modelData.name) } }
                            }
                        }

                        Row {
                            topPadding: Theme.spacingMd
                            spacing: Theme.spacingSm
                            Label { text: "Pass timings"; color: Theme.textSecondary
                                    font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                                    anchors.verticalCenter: parent.verticalCenter }
                            Label { text: "PASSPROBE · budget 8 ms"; color: Theme.textTertiary
                                    font.pixelSize: Theme.fontMeta; anchors.verticalCenter: parent.verticalCenter }
                        }
                        Item {
                            width: parent.width
                            height: 60
                            Label {
                                anchors.centerIn: parent
                                width: parent.width
                                horizontalAlignment: Text.AlignHCenter
                                text: (view.rev, dev.isHolding())
                                      ? "Waiting for PASSPROBE output on the log stream"
                                      : "Start a bench run to stream per-pass timings here"
                                color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }
            }
        }
    }

    component DevInstrumentRow: Item {
        id: dirow
        property string label: ""
        property string env: ""
        property bool checked: false
        signal toggledOn(bool on)
        height: 32
        Row {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            spacing: 9
            Label { text: dirow.label; color: Theme.textPrimary; font.pixelSize: Theme.fontBody13
                    anchors.verticalCenter: parent.verticalCenter }
            Label { visible: dirow.env !== ""; text: dirow.env; color: Theme.textTertiary
                    font.pixelSize: Theme.fontMicro; font.family: Theme.monoFamily
                    anchors.baseline: parent.children[0].baseline }
            // How far this toggle reaches. A mixed set where some instruments apply to the
            // running engine and others need a relaunch, with nothing saying which, is the
            // lying-control shape: both switches look identical and one of them does nothing
            // you can see. Only the live ones are marked - an unmarked row is bench-only,
            // which is still the majority.
            Label {
                visible: dirow.env !== "" && dev.instrumentReach(dirow.env) === "live"
                text: "live"
                color: Theme.success
                font.pixelSize: Theme.fontMicro
                anchors.baseline: parent.children[0].baseline
            }
        }
        ThemedSwitch {
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: parent.right
            checked: dirow.checked
            onToggled: dirow.toggledOn(checked)
        }
    }

    Rectangle {
        id: dock
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: 118
        color: Theme.base
        Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.border }

        Row {
            anchors.fill: parent
            anchors.topMargin: Theme.spacingSm
            anchors.bottomMargin: Theme.spacingSm
            anchors.leftMargin: Theme.spacingLg
            anchors.rightMargin: Theme.spacingLg
            spacing: Theme.spacingMd

            Item {
                width: Math.max(0, parent.width - 420 - Theme.spacingMd)
                height: parent.height
                Column {
                    anchors.fill: parent
                    spacing: Theme.spacingXs
                    Row {
                        width: parent.width
                        height: 16
                        Label { text: "Log - LWE-* only"; color: Theme.textSecondary
                                font.pixelSize: Theme.fontMeta; font.weight: Theme.weightMedium
                                anchors.verticalCenter: parent.verticalCenter }
                        Item { width: parent.width - x - followRow.width - journalRow.width
                                     - Theme.spacingMd; height: 1 }
                        // the live daemon's journal, folded into this same console. Off by
                        // default: DevView is never destroyed, so an always-on follower
                        // would run behind every other view for the whole session.
                        Row {
                            id: journalRow
                            spacing: Theme.spacingXs
                            anchors.verticalCenter: parent.verticalCenter
                            Label { text: "engine journal"; color: Theme.textTertiary
                                    font.pixelSize: Theme.fontMeta
                                    anchors.verticalCenter: parent.verticalCenter }
                            ThemedSwitch {
                                id: journalSwitch
                                objectName: "journalSwitch"
                                anchors.verticalCenter: parent.verticalCenter
                                pillWidth: 24; pillHeight: 14
                                checked: false
                                onCheckedChanged: checked ? dev.startJournal() : dev.stopJournal()
                            }
                        }
                        Item { width: Theme.spacingMd; height: 1 }
                        Row {
                            id: followRow
                            spacing: Theme.spacingXs
                            anchors.verticalCenter: parent.verticalCenter
                            Label { text: "follow"; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                                    anchors.verticalCenter: parent.verticalCenter }
                            ThemedSwitch {
                                id: followSwitch
                                anchors.verticalCenter: parent.verticalCenter
                                pillWidth: 24; pillHeight: 14
                                checked: true
                            }
                        }
                    }
                    ListView {
                        id: logView
                        width: parent.width
                        height: parent.height - y
                        clip: true
                        model: logModel
                        ScrollBar.vertical: ScrollBar {}
                        onCountChanged: if (followSwitch.checked) positionViewAtEnd()
                        delegate: Label {
                            required property var model
                            width: logView.width
                            text: model.line
                            color: model.line.toLowerCase().indexOf("error") >= 0 ? Theme.warning
                                   : model.line.toLowerCase().indexOf("watch") >= 0 ? Theme.warning
                                   : model.source === "journal" ? Theme.textTertiary
                                                                : Theme.textSecondary
                            font.pixelSize: Theme.fontMicro
                            font.family: Theme.monoFamily
                            wrapMode: Text.NoWrap
                            elide: Text.ElideRight
                        }
                        Label {
                            anchors.centerIn: parent
                            width: parent.width - Theme.spacingLg * 2
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            visible: logModel.count === 0
                            text: "LWE-* output from a bench run shows here. Turn on the engine "
                                + "journal to see the live daemon - expect it to be quiet: it "
                                + "logs lifecycle and failures, not instruments."
                            color: Theme.textTertiary
                            font.pixelSize: Theme.fontMeta
                        }
                    }
                }
            }
            Rectangle { width: 1; height: parent.height; color: Theme.border }
            Column {
                width: 420 - Theme.spacingMd
                height: parent.height
                spacing: Theme.spacingXs
                Row {
                    width: parent.width
                    spacing: Theme.spacingSm
                    TextField {
                        id: dockVerdict
                        width: parent.width - dockLog.width - Theme.spacingSm
                        height: 26
                        topPadding: 0; bottomPadding: 0; leftPadding: 10
                        placeholderText: "Verdict - one line, scene + time auto"
                        color: Theme.textPrimary; font.pixelSize: Theme.fontControl
                        background: Rectangle { color: Theme.surface; radius: Theme.radiusSm
                            border.width: 1; border.color: parent.activeFocus ? Theme.borderStrong : Theme.border }
                        onAccepted: dockLog.commit()
                    }
                    Rectangle {
                        id: dockLog
                        height: 26; width: dockLogLbl.implicitWidth + Theme.spacingLg
                        radius: Theme.radiusSm
                        color: dockLogHov.hovered ? Theme.hoverWash : Theme.surfaceVariant
                        border.width: 1; border.color: Theme.border
                        function commit() {
                            if (dockVerdict.text.trim() !== "") { dev.logVerdict(dockVerdict.text); dockVerdict.text = ""; }
                        }
                        Label { id: dockLogLbl; anchors.centerIn: parent; text: "Log"
                                color: Theme.textPrimary; font.pixelSize: Theme.fontControl }
                        HoverHandler { id: dockLogHov }
                        TapHandler { onTapped: dockLog.commit() }
                    }
                }
                Label {
                    width: parent.width
                    property var last: (view.rev, dev.recentVerdicts(1))
                    text: last.length > 0 ? ("Last: " + last[0].time + " " + last[0].scene +
                                             " - \"" + last[0].text + "\"")
                                          : "Last: no verdicts yet"
                    color: Theme.textTertiary; font.pixelSize: Theme.fontMeta
                    elide: Text.ElideRight
                }
                Row {
                    width: parent.width
                    spacing: Theme.spacingSm
                    property var runs: (view.rev, dev.runHistory())
                    Label {
                        text: parent.runs.length > 0
                              ? ("Last run: exit " + parent.runs[0].code + " · 200 lines kept")
                              : "Last run: none yet"
                        color: (parent.runs.length > 0 && parent.runs[0].code !== 0) ? Theme.danger
                                                                                    : Theme.textTertiary
                        font.pixelSize: Theme.fontMeta
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Item { width: parent.width - x - historyDoor.width; height: 1 }
                    Row {
                        id: historyDoor
                        spacing: Theme.spacingXs
                        anchors.verticalCenter: parent.verticalCenter
                        Label { text: "History"; color: Theme.textSecondary; font.pixelSize: Theme.fontMeta
                                anchors.verticalCenter: parent.verticalCenter }
                        IconChevron { direction: "down"; size: 12; color: Theme.textSecondary
                                anchors.verticalCenter: parent.verticalCenter }
                        // History opens the palette's Verdict tab (run history lives there)
                        TapHandler { onTapped: toolsLoader.openTab(4) }
                    }
                }
            }
        }
    }

    Loader {
        id: toolsLoader
        active: false
        function open() { active = true; if (item) { item.targetName = view.targetName; item.show(); } }
        function openTab(i) { open(); if (item) item.tab = i; }
        function toggle() { if (active && item && item.visible) item.close(); else open(); }
        sourceComponent: ToolsPalette {}
        onLoaded: { item.targetName = view.targetName; item.show(); }
    }
}
