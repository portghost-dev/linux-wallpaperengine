import QtQuick
import QtQuick.Controls.Basic
import "."

Rectangle {
    id: deck

    property var engineStatus: ({})
    property bool masterActive: false
    property bool rotationOn: true

    // the gear's doorway (sec 4.1): Main routes this to the editor pre-navigated to the
    // now-playing wallpaper - one door per destination, never a second instance
    signal openWallpaperPanel(string wid)

    height: 72
    color: Theme.base

    function _field(key) {
        var v = deck.engineStatus ? deck.engineStatus[key] : undefined;
        return (v === undefined || v === null || v === "" || v === "-") ? "" : String(v);
    }
    readonly property bool engineUp: (engineStatus.state || "") === "up"
    readonly property bool engineOff: !masterActive
    readonly property bool engineDown: !engineOff && deck._field("state") === ""

    // Smooth clock: the status poll arrives every 2s, and binding the timer text/bar
    // straight to it made the whole readout step in 2s chunks. Anchor the last polled
    // next_in to a wall-clock timestamp and interpolate between polls with a 500ms tick;
    // paused rotation freezes the interpolation (the engine's countdown is frozen too).
    property real _statNextIn: NaN
    property double _statAnchor: 0
    property double _nowMs: 0
    function _reanchor(ni) {
        _statNextIn = ni; _statAnchor = Date.now(); _nowMs = _statAnchor;
    }
    onEngineStatusChanged: {
        var ni = parseInt(_field("next_in"));
        if (isNaN(ni)) { _statNextIn = NaN; return; }
        // ONE clock: once anchored, a poll only CORRECTS on a real jump (wallpaper change,
        // resume, drift over 1.5s). Re-anchoring on every poll re-synced the display phase
        // to the engine's second boundary while the local tick kept its own phase - the
        // two beat against each other as an uneven cadence (long gap, short gap, repeat).
        if (isNaN(_statNextIn) || !rotationOn) { _reanchor(ni); return; }
        var predicted = _statNextIn - (Date.now() - _statAnchor) / 1000;
        if (Math.abs(predicted - ni) > 1.5)
            _reanchor(ni);
    }
    Timer {
        // 250ms: a seconds flip lands within a quarter second of true - below perception
        // for a clock readout - and the bar fill moves smoothly
        interval: 250; repeat: true
        running: !isNaN(deck._statNextIn) && !deck.holding
        onTriggered: deck._nowMs = Date.now()
    }
    function elapsedSecs() {
        if (isNaN(_statNextIn)) return -1;
        var iv = centerProgress.statusInterval();
        var extra = deck.rotationOn ? (_nowMs - _statAnchor) / 1000 : 0;
        return Math.max(0, Math.min(iv, iv - _statNextIn + extra));
    }

    function refreshRotation() { rotationOn = backend.getRotationEnabled() }
    // sessionOverride()/overrideReach() are slots, not NOTIFYing properties, so the override
    // icons cannot bind to them directly. This rev is what re-reads them - without it the
    // icons were a one-shot Component.onCompleted read that never resynced when Settings or
    // the Developer area changed the same OVERRIDE_* keys.
    property int overrideRev: 0
    Connections {
        target: backend
        function onSettingsChanged() { deck.refreshRotation(); deck.overrideRev++ }
    }
    Component.onCompleted: refreshRotation()

    // re-evaluate the dev-cockpit hold (bench / A/B) whenever it changes. abRunning()/isRunning()
    // are slots, not NOTIFYing properties, so the rev bump is what re-reads them.
    property int devRev: 0
    Connections { target: dev; function onStateChanged() { deck.devRev++ } }
    property int wizRev: 0
    Connections { target: wizardBridge; function onPhaseChanged() { deck.wizRev++ } }

    readonly property bool testing: bench.isTesting
    readonly property bool abOn: (deck.devRev, dev.abRunning())
    readonly property bool devHold: (deck.devRev, dev.isHolding()) && !deck.abOn && !deck.testing
    readonly property bool wizBenching: (deck.wizRev, wizardBridge.phase() === "p3")
    readonly property bool holding: deck.testing || deck.abOn || deck.devHold || deck.wizBenching
    // during a hold the transport + right column dim (the bench owns the display); off/engine-down
    // dims the transport + overrides but NOT the left block (F24 - the status message stays legible).
    // all three named bench modes dim the transport to 0.45 so the center breathing BenchBar
    // (the shared "lease cover") reads identically across Workshop / Editor / Developer benching.
    readonly property real transportDim: (deck.testing || deck.wizBenching || deck.devHold) ? 0.45
                                       : deck.abOn ? 0.4 : deck.engineOff ? 0.35 : 1
    readonly property real rightDim: deck.transportDim

    function fmtTime(sec) {
        if (isNaN(sec) || sec < 0) return "-:--";
        var m = Math.floor(sec / 60);
        var s = Math.floor(sec % 60);
        return m + ":" + (s < 10 ? "0" : "") + s;
    }

    Rectangle { width: parent.width; height: 1; color: Theme.border }


    // --- left (idle / engine-off / engine-down): now playing ----------------------------
    // The left block is exempt from the off-state dimming (F24): when the engine is off or the
    // engine is down the status dot + secondary line render at full opacity so they stay readable.
    Row {
        id: leftIdle
        objectName: "deckLeftIdle"
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: Theme.spacingLg
        spacing: Theme.spacingMd
        visible: !deck.testing && !deck.abOn && !deck.wizBenching && !deck.devHold
        readonly property string showingWid: deck._field("current")

        Rectangle {
            objectName: "deckStatusDot"
            width: 6; height: 6; radius: 3
            visible: deck.engineOff || deck.engineDown
            color: deck.engineDown ? Theme.danger : Theme.textTertiary
            anchors.verticalCenter: parent.verticalCenter
        }

        Label {
            objectName: "deckStatusText"
            visible: deck.engineOff || deck.engineDown
            anchors.verticalCenter: parent.verticalCenter
            text: deck.engineDown ? "Engine down" : "Engine off"
            color: Theme.textSecondary
            font.pixelSize: Theme.fontBody13
        }

        Row {
            visible: !deck.engineOff && !deck.engineDown
            spacing: Theme.spacingMd
            Rectangle {
                width: 44; height: 28
                radius: Theme.radiusXs
                color: Theme.surfaceVariant
                anchors.verticalCenter: parent.verticalCenter
                visible: Theme.usableWidth > 560
                clip: true
                Image {
                    anchors.fill: parent
                    source: leftIdle.showingWid !== "" ? backend.thumbUrl(leftIdle.showingWid) : ""
                    fillMode: Image.PreserveAspectCrop
                    sourceSize.width: Theme.previewCap
                    asynchronous: true
                }
            }
            Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Label {
                    text: "Now playing"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMeta
                    visible: !Theme.compact
                }
                Label {
                    width: Math.min(implicitWidth, Theme.compact ? 130 : 170)
                    elide: Text.ElideRight
                    text: {
                        var id = deck._field("current");
                        if (id === "") return "nothing";
                        var t = backend.titleOf(id);
                        return t !== "" ? t : id;
                    }
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontDeckName
                    font.weight: Theme.weightMedium
                }
            }
            Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                visible: deck._field("next_up") !== "" && !Theme.compact
                Label { text: "Next up"; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta }
                Label {
                    width: Math.min(implicitWidth, 116)
                    elide: Text.ElideRight
                    text: {
                        var id = deck._field("next_up");
                        var t = backend.titleOf(id);
                        return t !== "" ? t : id;
                    }
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontControl
                }
            }
        }
    }

    Row {
        id: leftTesting
        objectName: "deckLeftTesting"
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: Theme.spacingLg
        spacing: Theme.spacingMd
        // the bridges are mutually unaware, so a Test and an A/B hold CAN coexist (both hold
        // the same daemon standdown); the A/B face wins the render so the two rows never stack.
        visible: deck.testing && !deck.abOn

        Rectangle {
            width: 44; height: 28
            radius: Theme.radiusXs
            color: Theme.surfaceVariant
            anchors.verticalCenter: parent.verticalCenter
            clip: true
            Image {
                anchors.fill: parent
                source: deck._field("current") !== "" ? backend.thumbUrl(deck._field("current")) : ""
                fillMode: Image.PreserveAspectCrop
                sourceSize.width: Theme.previewCap
                asynchronous: true
            }
        }
        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Row {
                spacing: Theme.spacingXs
                Rectangle {
                    width: 6; height: 6; radius: 3; color: Theme.warning
                    anchors.verticalCenter: parent.verticalCenter
                }
                Label {
                    text: "Editor Benching"
                    color: Theme.warning
                    font.pixelSize: Theme.fontMeta
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Label {
                width: Math.min(implicitWidth, 200)
                elide: Text.ElideRight
                text: {
                    var id = deck._field("current");
                    var t = id !== "" ? backend.titleOf(id) : "";
                    return t !== "" ? t : (id !== "" ? id : "Draft");
                }
                color: Theme.textPrimary
                font.pixelSize: Theme.fontDeckName
                font.weight: Theme.weightMedium
            }
        }
    }

    Row {
        id: leftWizBench
        objectName: "deckLeftWizBench"
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: Theme.spacingLg
        spacing: Theme.spacingMd
        visible: deck.wizBenching

        property int loadRemaining: -1
        property int expiredTicks: 0
        Timer {
            interval: 1000; repeat: true; triggeredOnStart: true
            running: deck.wizBenching
            onTriggered: {
                leftWizBench.loadRemaining = wizardBridge.benchLoadRemaining();
                if (leftWizBench.loadRemaining === 0) {
                    leftWizBench.expiredTicks++;
                    if (leftWizBench.expiredTicks >= 3)
                        wizardBridge.killBench();
                } else {
                    leftWizBench.expiredTicks = 0;
                }
            }
        }

        Rectangle {
            width: 44; height: 28
            radius: Theme.radiusXs
            color: Theme.surfaceVariant
            anchors.verticalCenter: parent.verticalCenter
            clip: true
            Image {
                anchors.fill: parent
                source: (deck.wizRev, wizardBridge.wid() !== "") ? backend.thumbUrl(wizardBridge.wid()) : ""
                fillMode: Image.PreserveAspectCrop
                sourceSize.width: Theme.previewCap
                asynchronous: true
            }
        }
        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Row {
                spacing: Theme.spacingXs
                Rectangle {
                    width: 6; height: 6; radius: 3; color: Theme.warning
                    anchors.verticalCenter: parent.verticalCenter
                }
                Label {
                    text: "Workshop Benching"
                    color: Theme.warning
                    font.pixelSize: Theme.fontMeta
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Label {
                width: Math.min(implicitWidth, 200)
                elide: Text.ElideRight
                text: (deck.wizRev, wizardBridge.wpTitle())
                color: Theme.textPrimary
                font.pixelSize: Theme.fontDeckName
                font.weight: Theme.weightMedium
            }
        }
        // the load-lease countdown - shown only while the scene is still loading (>= 0); it
        // vanishes once the scene comes alive (benchLoadRemaining -> -1)
        Column {
            objectName: "deckWizLease"
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            visible: leftWizBench.loadRemaining >= 0
            Label { text: "Lease expires"; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta }
            Label {
                text: deck.fmtTime(leftWizBench.loadRemaining) + " · auto-stop"
                color: Theme.textSecondary
                font.pixelSize: Theme.fontControl
            }
        }
    }

    Row {
        id: leftDevBench
        objectName: "deckLeftDevBench"
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: Theme.spacingLg
        spacing: Theme.spacingMd
        visible: deck.devHold

        readonly property string devWid: (deck.devRev, dev.activeTargetWid())

        Rectangle {
            width: 44; height: 28
            radius: Theme.radiusXs
            color: Theme.surfaceVariant
            anchors.verticalCenter: parent.verticalCenter
            clip: true
            Image {
                anchors.fill: parent
                source: leftDevBench.devWid !== "" ? backend.thumbUrl(leftDevBench.devWid) : ""
                fillMode: Image.PreserveAspectCrop
                sourceSize.width: Theme.previewCap
                asynchronous: true
            }
        }
        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Row {
                spacing: Theme.spacingXs
                Rectangle {
                    width: 6; height: 6; radius: 3; color: Theme.warning
                    anchors.verticalCenter: parent.verticalCenter
                }
                Label {
                    text: "Developer Benching"
                    color: Theme.warning
                    font.pixelSize: Theme.fontMeta
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Label {
                width: Math.min(implicitWidth, 200)
                elide: Text.ElideRight
                text: {
                    var id = leftDevBench.devWid;
                    var t = id !== "" ? backend.titleOf(id) : "";
                    return t !== "" ? t : (id !== "" ? id : "Bench target");
                }
                color: Theme.textPrimary
                font.pixelSize: Theme.fontDeckName
                font.weight: Theme.weightMedium
            }
        }
    }

    Row {
        id: leftAB
        objectName: "deckLeftAB"
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: Theme.spacingLg
        spacing: Theme.spacingXl * 1.33
        visible: deck.abOn

        readonly property string abWid: (deck.devRev, dev.activeTargetWid())
        readonly property string abTitle: {
            var t = leftAB.abWid !== "" ? backend.titleOf(leftAB.abWid) : "";
            return t !== "" ? t : (leftAB.abWid !== "" ? leftAB.abWid : "Bench target");
        }
        component ABSide: Column {
            id: abSide
            property string sideLabel: ""
            property string monoText: ""
            spacing: 3
            Label { text: abSide.sideLabel; color: Theme.textTertiary; font.pixelSize: Theme.fontMeta }
            Row {
                spacing: Theme.spacingSm
                Rectangle {
                    width: 40; height: 25; radius: Theme.radiusXs
                    color: Theme.surfaceVariant
                    border.width: 1; border.color: Theme.border
                    anchors.verticalCenter: parent.verticalCenter
                    clip: true
                    Image {
                        anchors.fill: parent
                        source: leftAB.abWid !== "" ? backend.thumbUrl(leftAB.abWid) : ""
                        fillMode: Image.PreserveAspectCrop
                        sourceSize.width: Theme.previewCap
                        asynchronous: true
                    }
                }
                Label {
                    width: Math.min(implicitWidth, 150)
                    elide: Text.ElideRight
                    text: leftAB.abTitle
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontDeckName
                    font.weight: Theme.weightMedium
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Label {
                text: abSide.monoText
                color: Theme.textSecondary
                font.pixelSize: Theme.fontMicro
                font.family: Theme.monoFamily
            }
        }

        // no positional claims here: the exhibit windows are user-draggable, so a
        // "left half" label would lie the moment one is moved. The mono line carries the
        // fix state; the on-window chips + border colors carry WHERE.
        ABSide {
            sideLabel: "Playing on A"
            monoText: { var st = (deck.devRev, dev.abState()); return st.sideA || ""; }
        }
        ABSide {
            sideLabel: "Playing on B"
            monoText: { var st = (deck.devRev, dev.abState()); return st.sideB || ""; }
        }
    }


    Column {
        id: centerProgress
        anchors.centerIn: parent
        spacing: 8
        visible: !deck.abOn

        // the rotation interval: the engine status when it carries one, else the
        // ACTIVE PLAYLIST's configured interval - so the right-hand MM:SS is always real
        // even against an engine that does not report interval= yet.
        function statusInterval() {
            var iv = parseInt(deck._field("interval"));
            if (!isNaN(iv) && iv > 0) return iv;
            try {
                // the playlist INTERVAL is stored in canonical SECONDS regardless of the
                // display unit (setPlaylistInterval converts on write) - multiplying by 60
                // again turned 15 min into 900:00 on the deck
                var pl = backend.activePlaylist();
                return parseInt(pl.interval) || 900;
            } catch (e) { return 900; }
        }

        Item {
            id: barSlot
            readonly property int barWidth: Math.max(110, Math.min(240, Theme.usableWidth - 430))
            width: barSlot.barWidth
            height: 14
            anchors.horizontalCenter: parent.horizontalCenter
            BenchBar {
                objectName: "deckBenchBar"
                width: barSlot.barWidth
                anchors.verticalCenter: parent.verticalCenter
                visible: deck.holding
                // EXEMPT from transportDim: this bar is the bench's presence cue, not part of the
                // transport that recedes behind it. It carries its own breathing opacity.
            }
            Rectangle {
                width: barSlot.barWidth; height: 3; radius: 1.5
                anchors.verticalCenter: parent.verticalCenter
                color: Theme.border
                visible: !deck.holding
                opacity: deck.transportDim
                Rectangle {
                    width: parent.width * deck._progress()
                    height: parent.height
                    radius: 1.5
                    color: Theme.accent
                    opacity: deck.rotationOn ? 1 : 0.5
                }
            }
            Label {
                anchors.right: parent.left
                anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                visible: !deck.holding
                opacity: deck.transportDim
                text: {
                    var e = deck.elapsedSecs();
                    if (e < 0) return "-:--";   // elapsed unknown (engine not reporting)
                    return deck.fmtTime(Math.floor(e));
                }
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMeta
            }
            Label {
                anchors.left: parent.right
                anchors.leftMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                visible: !deck.holding
                opacity: deck.transportDim
                text: deck.fmtTime(centerProgress.statusInterval())
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMeta
            }
        }
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 16

            component TransportGlyph: Item {
                id: tg
                property bool forward: true
                signal tapped()
                width: 24; height: 24
                enabled: !deck.holding
                // these DO recede during a lease - they are genuinely disabled while a bench holds it
                opacity: (deck.engineUp ? 1 : 0.4) * deck.transportDim
                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radiusXs
                    color: tgHover.hovered ? Theme.hoverWash : "transparent"
                }
                Canvas {
                    // Canvas paints ONCE and reads its Theme color AT PAINT TIME, so a theme
                    // switch changed the binding but left the pixels alone (glyphs kept the old
                    // palette until an app restart). Theme.rev ticks on every theme change.
                    property int themeRev: Theme.rev
                    onThemeRevChanged: requestPaint()
                    anchors.centerIn: parent
                    width: 12; height: 12
                    onPaint: {
                        var c = getContext("2d");
                        c.reset();
                        c.fillStyle = Theme.textSecondary;
                        c.beginPath();
                        if (tg.forward) {
                            c.moveTo(1, 1); c.lineTo(8, 6); c.lineTo(1, 11); c.closePath();
                            c.rect(9, 1, 2, 10);
                        } else {
                            c.moveTo(11, 1); c.lineTo(4, 6); c.lineTo(11, 11); c.closePath();
                            c.rect(1, 1, 2, 10);
                        }
                        c.fill();
                    }
                }
                HoverHandler { id: tgHover }
                TapHandler { onTapped: tg.tapped() }
            }

            TransportGlyph { forward: false; onTapped: backend.rotatePrev() }

            Item {
                width: 28; height: 28

                Rectangle {
                    anchors.fill: parent
                    radius: 14
                    visible: !deck.holding
                    opacity: deck.transportDim
                    color: Theme.accent
                    Canvas {
                        // Canvas paints ONCE and reads its Theme color AT PAINT TIME, so a theme
                        // switch changed the binding but left the pixels alone (glyphs kept the old
                        // palette until an app restart). Theme.rev ticks on every theme change.
                        property int themeRev: Theme.rev
                        onThemeRevChanged: requestPaint()
                        id: playPauseGlyph
                        anchors.centerIn: parent
                        width: 12; height: 12
                        onPaint: {
                            var c = getContext("2d");
                            c.reset();
                            c.fillStyle = Theme.onAccent;
                            c.beginPath();
                            if (deck.rotationOn) {
                                c.rect(2, 1, 3, 10); c.rect(7, 1, 3, 10);
                            } else {
                                c.moveTo(2, 1); c.lineTo(11, 6); c.lineTo(2, 11); c.closePath();
                            }
                            c.fill();
                        }
                    }
                    Connections {
                        target: deck
                        function onRotationOnChanged() { playPauseGlyph.requestPaint() }
                    }
                    TapHandler {
                        onTapped: {
                            // toggle based on the current state, then re-read the persisted truth
                            // instead of flipping locally (a failed write must not desync the glyph).
                            backend.setPaused(deck.rotationOn);
                            deck.refreshRotation();
                        }
                    }
                }


                Rectangle {
                    anchors.fill: parent
                    radius: 14
                    objectName: "deckStopSquare"
                    visible: deck.wizBenching || deck.testing || deck.devHold
                    // EXEMPT from transportDim: this is the only LIVE control while a bench holds
                    // the lease, so it must not recede with the transport around it. It carries no
                    // opacity of its own - full strength, so the disc is the true amber and the
                    // square the true page ground.
                    // was an OUTLINED disc (surfaceVariant fill + 1px amber border) carrying an
                    // amber square. Inverted so the stop DOMINATES while a bench holds the lease:
                    // the disc is the full-bright amber and the square is punched back out to the
                    // page ground. Theme.base is the deck's own color, so the square reads black on
                    // dark themes and white on light ones with no isLight branch needed.
                    color: Theme.warning
                    Rectangle {
                        anchors.centerIn: parent
                        width: 10; height: 10; radius: 2
                        color: Theme.base
                    }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            // dispatch to the lease's OWNER - each bridge already owns the teardown
                            // its own surface performs, so the deck adds a second door, never a
                            // second implementation. Order matches the transportDim predicate.
                            if (deck.wizBenching)   wizardBridge.close();
                            else if (deck.testing)  bench.stopTest();
                            else if (deck.devHold)  dev.stopBench();
                        }
                    }
                }
            }

            TransportGlyph { forward: true; onTapped: backend.rotateNext() }
        }
    }

    // The deck's own ScheduleModal. A second instance rather than a hoisted global one: every
    // modal in this tree is owned by the surface that opens it (TrashWizard is instantiated in
    // both Library and Workshop), and there is no global modal host to hoist into. Safe to
    // duplicate because ScheduleModal re-reads every value from the backend in onOpened, so the
    // two instances share no mutable state. Parented to the window overlay so it centers on the
    // window instead of on this 72px strip.
    ScheduleModal {
        id: deckSchedModal
        parent: Overlay.overlay
    }


    Column {
        id: centerAB
        objectName: "deckCenterAB"
        anchors.centerIn: parent
        spacing: 7
        visible: deck.abOn

        property int abTick: 0
        Timer { interval: 1000; running: centerAB.visible; repeat: true; onTriggered: centerAB.abTick++ }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 7
            Rectangle {
                width: 6; height: 6; radius: 3; color: Theme.warning
                anchors.verticalCenter: parent.verticalCenter
            }
            Label {
                text: (centerAB.abTick, "A/B live · bench holds display · "
                       + deck.fmtTime(dev.uptimeSeconds()))
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMeta
                anchors.verticalCenter: parent.verticalCenter
            }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: dev.stopHold() }
        }
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Theme.spacingLg
            opacity: 0.4
            enabled: false

            Canvas {   // prev outline
                // Canvas paints ONCE and reads its Theme color AT PAINT TIME, so a theme
                // switch changed the binding but left the pixels alone (glyphs kept the old
                // palette until an app restart). Theme.rev ticks on every theme change.
                property int themeRev: Theme.rev
                onThemeRevChanged: requestPaint()
                width: 12; height: 12; anchors.verticalCenter: parent.verticalCenter
                onPaint: {
                    var c = getContext("2d"); c.reset();
                    c.fillStyle = Theme.textSecondary; c.beginPath();
                    c.rect(0, 1, 2, 10);
                    c.moveTo(11, 1); c.lineTo(4, 6); c.lineTo(11, 11); c.closePath();
                    c.fill();
                }
            }
            Rectangle {
                width: 26; height: 26; radius: 13
                color: Theme.surfaceVariant
                border.width: 1; border.color: Theme.borderStrong
                anchors.verticalCenter: parent.verticalCenter
                Canvas {
                    // Canvas paints ONCE and reads its Theme color AT PAINT TIME, so a theme
                    // switch changed the binding but left the pixels alone (glyphs kept the old
                    // palette until an app restart). Theme.rev ticks on every theme change.
                    property int themeRev: Theme.rev
                    onThemeRevChanged: requestPaint()
                    anchors.centerIn: parent
                    anchors.horizontalCenterOffset: 1
                    width: 11; height: 11
                    onPaint: {
                        var c = getContext("2d"); c.reset();
                        c.fillStyle = Theme.textSecondary; c.beginPath();
                        c.moveTo(1, 0); c.lineTo(10, 5.5); c.lineTo(1, 11); c.closePath();
                        c.fill();
                    }
                }
            }
            Canvas {   // next outline
                // Canvas paints ONCE and reads its Theme color AT PAINT TIME, so a theme
                // switch changed the binding but left the pixels alone (glyphs kept the old
                // palette until an app restart). Theme.rev ticks on every theme change.
                property int themeRev: Theme.rev
                onThemeRevChanged: requestPaint()
                width: 12; height: 12; anchors.verticalCenter: parent.verticalCenter
                onPaint: {
                    var c = getContext("2d"); c.reset();
                    c.fillStyle = Theme.textSecondary; c.beginPath();
                    c.moveTo(1, 1); c.lineTo(8, 6); c.lineTo(1, 11); c.closePath();
                    c.rect(10, 1, 2, 10);
                    c.fill();
                }
            }
        }
    }

    Column {
        anchors.verticalCenter: parent.verticalCenter
        anchors.right: parent.right
        anchors.rightMargin: Theme.spacingLg
        spacing: 7
        opacity: deck.rightDim

        // PlaylistStrip.qml is another surface's file - its anchor slot stays as-is.
        PlaylistStrip {
            anchors.right: parent.right
            opensUp: true
            foldable: true   // deck shed step 3: mode + interval fold into the playlist menu
            // the schedule doorway lives on the strip, but the modal it opens must center on the
            // WINDOW, not inside the 72px deck - hence the overlay-parented instance below.
            onScheduleRequested: deckSchedModal.open()
        }

        Row {
            anchors.right: parent.right
            spacing: Theme.spacingSm

            // One icon button. iconKind selects which glyph the Canvas draws; the glyph is
            // painted in oi.ink (textPrimary when the feature is on, tertiary when off). All
            // shapes read oi.ink directly (one hop to the component root) so a state flip
            // repaints reliably.
            component OverrideIcon: Item {
                id: oi
                property string key: ""
                property string iconKind: ""
                property string title: ""     // plain-language name for the hover text
                // the feature's state, re-read whenever ANY surface changes the settings
                readonly property bool on: (deck.overrideRev, !backend.sessionOverride(oi.key))
                // "live" reaches the running scene; "next" lands on the next wallpaper. Derived
                // from the backend so landing set-volume/set-mouse clears the marks by itself.
                readonly property string reach: (deck.overrideRev, backend.overrideReach(oi.key))
                width: 24; height: 24

                readonly property color ink: oi.on ? Theme.textPrimary : Theme.textTertiary

                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radiusSm
                    color: oiHover.hovered ? Theme.hoverWash : "transparent"
                }
                Canvas {
                    // Canvas paints ONCE and reads its Theme color AT PAINT TIME, so a theme
                    // switch changed the binding but left the pixels alone (glyphs kept the old
                    // palette until an app restart). Theme.rev ticks on every theme change.
                    property int themeRev: Theme.rev
                    onThemeRevChanged: requestPaint()
                    id: glyph
                    anchors.centerIn: parent
                    width: 16; height: 16
                    onPaint: {
                        var c = getContext("2d");
                        c.reset();
                        c.fillStyle = oi.ink;
                        c.strokeStyle = oi.ink;
                        c.lineWidth = 1.5;
                        if (oi.iconKind === "sound") {
                            c.fillRect(1, 6.5, 3, 3);
                            c.beginPath();
                            c.moveTo(4, 8); c.lineTo(8, 3); c.lineTo(8, 13); c.closePath();
                            c.fill();
                            c.lineCap = "round";
                            for (var wv = 0; wv < 2; wv++) {
                                c.beginPath();
                                c.arc(8.5, 8, 2.5 + wv * 2.6, -Math.PI / 3, Math.PI / 3);
                                c.stroke();
                            }
                        } else if (oi.iconKind === "audio") {
                            c.beginPath();
                            if (c.roundRect) c.roundRect(5.5, 1, 5, 8.5, 2.5);
                            else { c.rect(5.5, 1, 5, 8.5); }
                            c.fill();
                            c.lineCap = "round";
                            c.beginPath();
                            c.arc(8, 8, 4.5, 0, Math.PI);
                            c.stroke();
                            c.beginPath();
                            c.moveTo(8, 12.5); c.lineTo(8, 14.5);
                            c.moveTo(5.5, 14.5); c.lineTo(10.5, 14.5);
                            c.stroke();
                        } else if (oi.iconKind === "camera") {
                            c.fillRect(1, 6.5, 9, 7);
                            c.beginPath();
                            c.moveTo(10, 8.5); c.lineTo(14.5, 6.8);
                            c.lineTo(14.5, 13.2); c.lineTo(10, 11.5); c.closePath();
                            c.fill();
                            for (var rl = 0; rl < 2; rl++) {
                                c.beginPath();
                                c.arc(3.4 + rl * 4.4, 3.6, 2.5, 0, Math.PI * 2);
                                c.stroke();
                            }
                        } else if (oi.iconKind === "pointer") {
                            c.beginPath();
                            c.moveTo(3, 1); c.lineTo(3, 13); c.lineTo(6, 10); c.lineTo(8.5, 15);
                            c.lineTo(10.5, 14); c.lineTo(8, 9); c.lineTo(12, 8.5); c.closePath();
                            c.fill();
                        }
                    }
                    Connections {
                        target: oi
                        function onInkChanged() { glyph.requestPaint() }
                    }
                }
                Rectangle {
                    visible: !oi.on
                    width: 20; height: 1.5; radius: 1
                    color: oi.ink
                    anchors.centerIn: parent
                    rotation: 45
                }

                // REACH MARK. A control that looks instant and is not is the lying-toggle
                // shape. DevView marks the LIVE rows because live is the minority there; on
                // this row the risk runs the other way, so the mark goes on the ones that do
                // NOT reach the running scene. Quiet tertiary dot - reach is a property of the
                // control, not an on/off state, so it does not take a state color.
                Rectangle {
                    visible: oi.reach !== "live"
                    width: 3; height: 3; radius: 1.5
                    color: Theme.textTertiary
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.topMargin: 1
                    anchors.rightMargin: 1
                }

                HoverHandler { id: oiHover }
                ToolTip.visible: oiHover.hovered
                ToolTip.delay: 400
                ToolTip.text: oi.title + (oi.on ? " on" : " off")
                              + (oi.reach === "live" ? "" : " - applies to the next wallpaper")
                TapHandler {
                    // `on` is a binding now, so the tap writes the SETTING and lets the
                    // settingsChanged rev bump re-read it. Passing oi.on is correct in both
                    // directions: feature on -> write the override true (force off), and back.
                    onTapped: backend.setSessionOverride(oi.key, oi.on)
                }
            }

            // shed step 4 (<600): the five buttons collapse into one overflow. `expanded` is
            // the gate; a Row skips invisible children, so no restructuring is needed.
            readonly property bool expanded: Theme.usableWidth >= 600

            OverrideIcon { key: "mute";  iconKind: "sound"; title: "Mute"
                           objectName: "overrideMute"; visible: parent.expanded }
            OverrideIcon { key: "audio"; iconKind: "audio"; title: "Audio response"
                           objectName: "overrideAudio"; visible: parent.expanded }
            OverrideIcon { key: "parallax"; iconKind: "camera";  title: "Mouse camera"
                           objectName: "overrideParallax"; visible: parent.expanded }
            OverrideIcon { key: "mouse";    iconKind: "pointer"; title: "Mouse objects"
                           objectName: "overridePointer"; visible: parent.expanded }

            Item {
                id: utilOverflow
                objectName: "deckUtilOverflow"
                width: 26; height: 24
                visible: !parent.expanded
                anchors.verticalCenter: parent.verticalCenter

                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radiusSm
                    color: (ovHover.hovered || ovPop.visible) ? Theme.hoverWash : "transparent"
                }
                Row {
                    anchors.centerIn: parent
                    spacing: 3
                    Repeater {
                        model: 3
                        delegate: Rectangle {
                            width: 3; height: 3; radius: 1.5
                            color: ovHover.hovered ? Theme.textPrimary : Theme.textSecondary
                        }
                    }
                }
                HoverHandler { id: ovHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    // same press-outside-then-reopen race the strip menus and the header funnel
                    // guard against: by tap time the popup has already closed itself
                    onTapped: {
                        if (ovPop.visible) ovPop.close();
                        else if (!ovPop.justClosed) ovPop.open();
                    }
                }

                Popup {
                    id: ovPop
                    y: -height - 6
                    x: utilOverflow.width - width
                    padding: Theme.spacingXs
                    property bool justClosed: false
                    onClosed: { justClosed = true; ovGuard.restart() }
                    Timer { id: ovGuard; interval: 150; onTriggered: ovPop.justClosed = false }
                    background: Rectangle {
                        color: Theme.surfaceVariant
                        radius: Theme.radiusMd
                        border.width: 1
                        border.color: Theme.borderStrong
                    }
                    contentItem: Column {
                        spacing: 0
                        Repeater {
                            model: [{k: "mute",     t: "Mute"},
                                    {k: "audio",    t: "Audio response"},
                                    {k: "parallax", t: "Mouse camera"},
                                    {k: "mouse",    t: "Mouse objects"}]
                            delegate: Item {
                                id: ovRow
                                required property var modelData
                                readonly property bool on: (deck.overrideRev,
                                                            !backend.sessionOverride(modelData.k))
                                readonly property string reach: (deck.overrideRev,
                                                                 backend.overrideReach(modelData.k))
                                width: 210; height: 28
                                Rectangle {
                                    anchors.fill: parent
                                    radius: Theme.radiusXs
                                    color: ovRowHov.hovered ? Theme.hoverWash : "transparent"
                                }
                                Label {
                                    anchors.left: parent.left
                                    anchors.leftMargin: Theme.spacingSm
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: ovRow.modelData.t
                                    color: ovRow.on ? Theme.textPrimary : Theme.textTertiary
                                    font.pixelSize: Theme.fontControl
                                }
                                Label {
                                    anchors.right: parent.right
                                    anchors.rightMargin: Theme.spacingSm
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: ovRow.on ? (ovRow.reach === "live" ? "on" : "on (next)")
                                                   : "off"
                                    color: Theme.textTertiary
                                    font.pixelSize: Theme.fontMeta
                                }
                                HoverHandler { id: ovRowHov; cursorShape: Qt.PointingHandCursor }
                                TapHandler {
                                    onTapped: backend.setSessionOverride(ovRow.modelData.k, ovRow.on)
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                width: 1; height: 14; color: Theme.border
                anchors.verticalCenter: parent.verticalCenter
            }

            Item {
                id: animPauseBtn
                objectName: "deckAnimPause"
                width: 24; height: 24
                anchors.verticalCenter: parent.verticalCenter
                // frozen == the engine's own answer, from TWO sources of the same truth:
                // the 2 s status poll, and the set-speed done reply captured at tap time.
                // The tap echo exists because waiting for the poll made the glyph lag the
                // click by up to 2 s while the scene itself froze next frame. confirmedSpeed
                // is NOT optimistic state - it is the engine's reply - and every poll tick
                // clears it, handing truth back to status (which by then carries the same
                // value, since the set completed before that poll was taken).
                property real confirmedSpeed: NaN
                Connections {
                    target: deck
                    function onEngineStatusChanged() { animPauseBtn.confirmedSpeed = NaN }
                }
                // absent speed everywhere (engine down / old daemon binary without the
                // echo) reads as not-frozen and the tap is a no-op
                readonly property real speed: {
                    if (!isNaN(confirmedSpeed))
                        return confirmedSpeed;
                    var v = Number(deck.engineStatus.speed);
                    return isNaN(v) ? 1 : v;
                }
                readonly property bool frozen: speed === 0

                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radiusSm
                    color: animPauseBtn.frozen
                           ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.15)
                           : (apHover.hovered ? Theme.hoverWash : "transparent")
                }
                Canvas {
                    id: apGlyph
                    anchors.centerIn: parent
                    width: 16; height: 16
                    // Canvas paints ONCE and reads its Theme color AT PAINT TIME (same note
                    // as every other deck glyph); repaint on theme change AND state flip
                    property int themeRev: Theme.rev
                    onThemeRevChanged: requestPaint()
                    readonly property color ink: animPauseBtn.frozen ? Theme.accent
                                               : apHover.hovered ? Theme.textPrimary
                                                                 : Theme.textSecondary
                    onInkChanged: requestPaint()
                    onPaint: {
                        var c = getContext("2d");
                        c.reset();
                        c.fillStyle = apGlyph.ink;
                        if (animPauseBtn.frozen) {
                            c.beginPath();
                            c.moveTo(4, 2.5); c.lineTo(13.5, 8); c.lineTo(4, 13.5);
                            c.closePath(); c.fill();
                        } else {
                            c.fillRect(4, 2.5, 2.6, 11);
                            c.fillRect(9.4, 2.5, 2.6, 11);
                        }
                    }
                    Connections {
                        target: animPauseBtn
                        function onFrozenChanged() { apGlyph.requestPaint() }
                    }
                }
                HoverHandler { id: apHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        var confirmed = backend.setAnimationFrozen(!animPauseBtn.frozen);
                        // -1 = engine unreachable/rejected: the glyph does not move
                        if (confirmed >= 0)
                            animPauseBtn.confirmedSpeed = confirmed;
                    }
                }
            }

            Item {
                id: gearBtn
                objectName: "deckGear"
                width: 24; height: 24
                visible: !deck.holding
                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radiusSm
                    color: gearHover.hovered ? Theme.hoverWash : "transparent"
                }
                IconGear {
                    anchors.centerIn: parent
                    size: 16
                    color: gearHover.hovered ? Theme.textPrimary : Theme.textTertiary
                }
                HoverHandler { id: gearHover; cursorShape: Qt.PointingHandCursor }

                ToolTip.visible: gearHover.hovered
                ToolTip.delay: 400
                ToolTip.text: "Wallpaper settings"

                TapHandler {
                    onTapped: {
                        if (deck._field("current") === "")
                            return;
                        if (settingsPopup.visible)
                            settingsPopup.close();
                        else if (!settingsPopup.justClosed)
                            settingsPopup.open();
                    }
                }

                DeckSettingsPopup {
                    id: settingsPopup
                    objectName: "deckSettingsPopup"
                    engineStatus: deck.engineStatus
                    // usable room above / below the gear, the 8 px window margin and the 8 px
                    // gap to the gear already subtracted. The deck is anchored to the window
                    // bottom, so deck.y + deck.height IS the window height and both bindings
                    // re-evaluate on any resize.
                    spaceAbove: (deck.y, deck.height, gearBtn.mapToItem(null, 0, 0).y - 16)
                    spaceBelow: (deck.y + deck.height)
                               - (gearBtn.mapToItem(null, 0, 0).y + gearBtn.height) - 16
                    // a press outside closes the popup, and the gear IS outside it - without
                    // this guard the same tap would immediately reopen it
                    property bool justClosed: false
                    onClosed: { justClosed = true; popupGuard.restart() }
                    Timer { id: popupGuard; interval: 150
                            onTriggered: settingsPopup.justClosed = false }
                    onOpenEditorRequested: function(wid) { deck.openWallpaperPanel(wid) }
                }
            }
        }
    }

    function _progress() {
        var e = deck.elapsedSecs();
        var iv = centerProgress.statusInterval();
        if (e < 0 || iv <= 0)
            return 0;
        return Math.max(0, Math.min(1, e / iv));
    }
}
