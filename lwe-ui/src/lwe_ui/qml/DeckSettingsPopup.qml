import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import "."

Popup {
    id: pop

    property var engineStatus: ({})
    property real spaceAbove: 0     // usable height above the gear, margins already subtracted
    property real spaceBelow: 0     // same, below

    signal openEditorRequested(string wid)

    readonly property color markColor: Theme.accent
    readonly property color failColor: Theme.danger

    readonly property real bleedLeft: 6
    readonly property real bleedRight: pop.rightPadding - 1

    readonly property real fontRow: 12.5
    readonly property real fontBanner: 11.5
    readonly property real fontMono: 11.5

    // bumped on every bridge state change; slot reads bind through it
    property int rev: 0
    // bumped only when the scene-property SET changes; the props model binds through
    // THIS so a speed/volume/scaling commit cannot rebuild the property delegates
    // mid-gesture (H28 pattern, mirrors the editor's propRev)
    property int propRev: 0
    Connections {
        target: deckPopup
        function onStateChanged() { pop.rev++ }
        function onPropsEdited() { pop.propRev++ }
        function onCommitFailed(keys) { pop.raiseFailure(keys) }
    }

    property var failedKeys: []
    function raiseFailure(keys) {
        pop.failedKeys = keys;
        failClear.restart();
    }
    Timer {
        id: failClear
        interval: 2500
        onTriggered: pop.failedKeys = []
    }
    function isFailed(key) { return key !== "" && pop.failedKeys.indexOf(key) >= 0 }
    function isMarked(key) { return (pop.rev, key !== "" && deckPopup.isMarked(key)) }

    // --- Speed mapping: four-zone piecewise logarithmic over 0.1x..10x --------------------
    //
    // A single linear 0.25..2.0 band spent most of the travel on rates nobody wants and gave
    // the useful 1x..2x region a few pixels. These four zones buy resolution where the hand
    // actually works: 35% of the whole track covers 1x..2x alone, while 0.1x and 10x stay
    // reachable at the ends. Each zone is log-smooth (constant RATIO per pixel, which is what
    // "feels even" for a speed), and the zones meet exactly at their knots, so the curve is
    // continuous - there is no jump crossing 1.0x, 2.0x or 5.0x.
    //
    //   position   0%     30%    65%    87%   100%
    //   speed     0.1x   1.0x   2.0x   5.0x  10.0x
    readonly property var speedKnotPos:   [0.0, 0.30, 0.65, 0.87, 1.0]
    readonly property var speedKnotValue: [0.1, 1.0,  2.0,  5.0,  10.0]
    readonly property real speedDetent: 1.0
    readonly property real speedDetentBand: 0.02   // snap when within ~2% of 1.0x

    // the 1.0x hash tick sits at the first interior knot
    readonly property real speedDetentPos: speedKnotPos[1]

    function speedForPos(p) {
        var pos = Math.max(0, Math.min(1, p));
        for (var i = 0; i < pop.speedKnotPos.length - 1; i++) {
            var p0 = pop.speedKnotPos[i], p1 = pop.speedKnotPos[i + 1];
            if (pos > p1 && i < pop.speedKnotPos.length - 2)
                continue;
            var v0 = pop.speedKnotValue[i], v1 = pop.speedKnotValue[i + 1];
            var t = (p1 === p0) ? 0 : (pos - p0) / (p1 - p0);
            return v0 * Math.pow(v1 / v0, t);       // log-smooth inside the zone
        }
        return pop.speedKnotValue[pop.speedKnotValue.length - 1];
    }

    function posForSpeed(v) {
        var s = Math.max(pop.speedKnotValue[0],
                         Math.min(pop.speedKnotValue[pop.speedKnotValue.length - 1], Number(v)));
        for (var i = 0; i < pop.speedKnotValue.length - 1; i++) {
            var v0 = pop.speedKnotValue[i], v1 = pop.speedKnotValue[i + 1];
            if (s > v1 && i < pop.speedKnotValue.length - 2)
                continue;
            var p0 = pop.speedKnotPos[i], p1 = pop.speedKnotPos[i + 1];
            return p0 + (p1 - p0) * (Math.log(s / v0) / Math.log(v1 / v0));
        }
        return 1.0;
    }

    // light detent: a computed rate that lands within the band reads as exactly 1.0x
    function speedDetented(v) {
        return Math.abs(v - pop.speedDetent) <= pop.speedDetentBand ? pop.speedDetent : v;
    }

    // two significant figures, with a decimal kept below 10x so the detent reads 1.0x
    function speedText(v) {
        var r = Number(Number(v).toPrecision(2));
        return ((r < 10 && r === Math.round(r)) ? r.toFixed(1) : String(r)) + "x";
    }

    readonly property bool flipBelow: spaceAbove < 200
    readonly property real capHeight: Math.max(140, flipBelow ? spaceBelow : spaceAbove)

    width: 340
    // right edges flush with the gear; Popup.margins clamps the card inside the window
    x: (parent ? parent.width : 0) - width
    y: flipBelow ? ((parent ? parent.height : 0) + 8) : (-height - 8)
    margins: 8

    topPadding: 14
    bottomPadding: 14
    leftPadding: 16
    rightPadding: 16

    modal: false
    dim: false
    // a non-modal popup does not take key focus by default, and without focus CloseOnEscape
    // never sees the key. The window's own Escape shortcut already steps aside for a visible
    // overlay child (qml/Main.qml:86-100), so this takes Escape without ejecting the view.
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Rectangle {
        color: Theme.surface
        radius: 8
        border.width: 1
        border.color: Theme.borderStrong
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: "#000000"
            shadowOpacity: 0.5
            blurMax: 28
            shadowBlur: 1.0
            shadowVerticalOffset: 8
            shadowHorizontalOffset: 0
        }
    }

    readonly property real bodyCap: Math.max(
        60, capHeight - topPadding - bottomPadding - head.height - banner.height)

    // Snapshot capture is the CURRENT TRANSITION, not the first popup interaction: this item
    // lives in the deck whether the popup is shown or not, so the shared 2 s status poll walks
    // the bridge onto each new wallpaper as it starts playing. Marks clear there too.
    onEngineStatusChanged: deckPopup.syncCurrent(String(pop.engineStatus.current || ""))
    Component.onCompleted: deckPopup.syncCurrent(String(pop.engineStatus.current || ""))

    component PRow: Item {
        id: prow
        property string label: ""
        property string caption: ""
        property bool indented: false
        default property alias slot: holder.data
        width: parent ? parent.width : 0
        implicitHeight: Math.max(34, labelCol.implicitHeight + 8, holder.childrenRect.height)

        Rectangle {
            visible: prow.indented
            x: 5
            width: 1
            y: 4
            height: parent.height - 8
            color: Theme.border
        }
        Column {
            id: labelCol
            anchors.left: parent.left
            anchors.leftMargin: prow.indented ? 18 : 0
            anchors.right: holder.left
            anchors.rightMargin: Theme.spacingSm
            anchors.verticalCenter: parent.verticalCenter
            spacing: 1
            Label {
                width: parent.width
                text: prow.label
                color: Theme.textPrimary
                font.pixelSize: pop.fontRow
                elide: Text.ElideRight
            }
            Label {
                width: parent.width
                // no `height: visible ? implicitHeight : 0` here: on an eliding Label that
                // binding is a height/implicitHeight loop, and it is redundant anyway - the
                // enclosing Column already drops an invisible child from its layout entirely
                visible: prow.caption !== ""
                text: prow.caption
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMicro
                elide: Text.ElideRight
            }
        }
        Item {
            id: holder
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: childrenRect.width
            height: childrenRect.height
        }
    }

    component PHead: Item {
        id: phead
        property string label: ""
        property string caption: ""
        width: parent ? parent.width : 0
        height: 24
        Label {
            id: pheadLabel
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: phead.label
            color: Theme.textSecondary
            font.pixelSize: Theme.fontMeta
            font.weight: Theme.weightMedium
        }
        Label {
            anchors.right: parent.right
            anchors.baseline: pheadLabel.baseline
            visible: phead.caption !== ""
            text: phead.caption
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMicro
        }
    }

    component PSlider: Slider {
        id: sld
        property string ckey: ""
        // 0..1 position of a hash tick on the track; -1 draws none
        property real tickAt: -1
        signal commit(real v)
        width: 132
        implicitWidth: 132
        implicitHeight: 16
        onPressedChanged: if (!pressed) sld.commit(sld.value)
        background: Rectangle {
            x: sld.leftPadding
            y: sld.topPadding + sld.availableHeight / 2 - height / 2
            width: sld.availableWidth
            height: 3
            radius: 1.5
            color: Theme.border
            Rectangle {
                width: sld.visualPosition * parent.width
                height: parent.height
                radius: 1.5
                color: Theme.accent
            }
            // detent hash: a tick THROUGH the track, so it reads at any fill level
            Rectangle {
                visible: sld.tickAt >= 0
                x: sld.tickAt * parent.width - 0.5
                y: -3
                width: 1
                height: 9
                color: Theme.textTertiary
            }
        }
        handle: Rectangle {
            x: sld.leftPadding + sld.visualPosition * (sld.availableWidth - width)
            y: sld.topPadding + sld.availableHeight / 2 - height / 2
            width: 10
            height: 10
            radius: 5
            color: Theme.textPrimary
        }
    }

    component PChip: Rectangle {
        id: chip
        property string ckey: ""
        property string text: ""
        property bool editing: false
        signal entered(string text)
        height: 22
        width: Math.max(46, chipLabel.implicitWidth + 16)
        radius: 5
        color: Theme.surface
        border.width: pop.isFailed(ckey) ? 1.5 : 1
        border.color: pop.isFailed(ckey) ? pop.failColor
                    : (pop.isMarked(ckey) ? pop.markColor : Theme.border)
        Label {
            id: chipLabel
            visible: !chip.editing
            anchors.centerIn: parent
            text: chip.text
            color: Theme.textPrimary
            font.pixelSize: Theme.fontMeta
        }
        TextInput {
            id: chipEdit
            visible: chip.editing
            anchors.fill: parent
            anchors.leftMargin: 8
            anchors.rightMargin: 8
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignHCenter
            color: Theme.textPrimary
            font.pixelSize: Theme.fontMeta
            selectByMouse: true
            Keys.onEscapePressed: { chip.editing = false; chipEdit.focus = false }
            onEditingFinished: {
                if (!chip.editing)
                    return;
                chip.editing = false;
                chip.entered(chipEdit.text);
            }
        }
        HoverHandler { cursorShape: Qt.IBeamCursor }
        TapHandler {
            onTapped: {
                chipEdit.text = chip.text;
                chip.editing = true;
                chipEdit.forceActiveFocus();
                chipEdit.selectAll();
            }
        }
    }

    // dropdown: the caller fills `entries` with {label, value} and handles picked()
    component PDrop: Rectangle {
        id: drop
        property string ckey: ""
        property var entries: []
        property string current: ""
        property string display: ""
        property bool compact: false
        property bool editable: false
        property bool editing: false
        signal picked(string value)
        signal entered(string text)
        height: compact ? 24 : 26
        width: compact ? 78 : Math.max(96, dropLabel.implicitWidth + (compact ? 26 : 32))
        radius: compact ? 5 : 6
        color: Theme.surface
        border.width: pop.isFailed(ckey) ? 1.5 : 1
        border.color: pop.isFailed(ckey) ? pop.failColor
                    : (pop.isMarked(ckey) ? pop.markColor : Theme.border)
        Label {
            id: dropLabel
            visible: !drop.editing
            anchors.left: parent.left
            anchors.leftMargin: drop.compact ? 8 : 10
            anchors.verticalCenter: parent.verticalCenter
            text: drop.display
            color: Theme.textPrimary
            font.pixelSize: drop.compact ? Theme.fontMeta : Theme.fontControl
            elide: Text.ElideRight
        }
        TextInput {
            id: dropEdit
            visible: drop.editing
            anchors.left: parent.left
            anchors.leftMargin: drop.compact ? 8 : 10
            anchors.right: caret.left
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            color: Theme.textPrimary
            font.pixelSize: drop.compact ? Theme.fontMeta : Theme.fontControl
            selectByMouse: true
            Keys.onEscapePressed: { drop.editing = false; dropEdit.focus = false }
            onEditingFinished: {
                if (!drop.editing)
                    return;
                drop.editing = false;
                drop.entered(dropEdit.text);
            }
        }
        Item {
            id: caret
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: drop.compact ? 22 : 26
            IconChevron {
                anchors.right: parent.right
                anchors.rightMargin: drop.compact ? 6 : 8
                anchors.verticalCenter: parent.verticalCenter
                direction: "down"
                size: 10
                color: Theme.textSecondary
            }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler {
                onTapped: {
                    drop.editing = false;
                    if (dropMenu.visible) dropMenu.close();
                    else if (!dropMenu.justClosed) dropMenu.open();
                }
            }
        }
        Item {
            anchors.left: parent.left
            anchors.right: caret.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            HoverHandler { cursorShape: drop.editable ? Qt.IBeamCursor : Qt.PointingHandCursor }
            TapHandler {
                onTapped: {
                    if (drop.editable) {
                        dropEdit.text = drop.display;
                        drop.editing = true;
                        dropEdit.forceActiveFocus();
                        dropEdit.selectAll();
                        return;
                    }
                    if (dropMenu.visible) dropMenu.close();
                    else if (!dropMenu.justClosed) dropMenu.open();
                }
            }
        }
        Menu {
            id: dropMenu
            y: drop.height + 2
            property bool justClosed: false
            onClosed: { justClosed = true; dropGuard.restart() }
            Timer { id: dropGuard; interval: 150; onTriggered: dropMenu.justClosed = false }
            background: Rectangle {
                implicitWidth: 150
                color: Theme.surfaceVariant
                radius: Theme.radiusSm
                border.width: 1
                border.color: Theme.borderStrong
            }
            Repeater {
                model: drop.entries
                delegate: ThemedMenuItem {
                    required property var modelData
                    text: modelData.label
                    onTriggered: drop.picked(String(modelData.value))
                }
            }
        }
    }

    // a toggle wearing the mark: the pill's own border IS the track, so the mark is drawn as
    // an outline around it rather than replacing a border that carries state
    component PToggle: Item {
        id: tgl
        property string ckey: ""
        property bool checked: false
        signal toggled(bool on)
        implicitWidth: 34
        implicitHeight: 21
        Rectangle {
            anchors.centerIn: parent
            width: 34
            height: 21
            radius: 11
            color: "transparent"
            visible: pop.isFailed(tgl.ckey) || pop.isMarked(tgl.ckey)
            border.width: pop.isFailed(tgl.ckey) ? 1.5 : 1
            border.color: pop.isFailed(tgl.ckey) ? pop.failColor : pop.markColor
        }
        ThemedSwitch {
            anchors.centerIn: parent
            checked: tgl.checked
            onToggled: tgl.toggled(checked)
        }
    }

    component PVerb: Rectangle {
        id: verb
        property string label: ""
        signal activated()
        height: 24
        width: verbLabel.implicitWidth + 18
        radius: 5
        color: verbHover.hovered ? Theme.hoverWash : "transparent"
        border.width: 1
        border.color: Theme.borderStrong
        Label {
            id: verbLabel
            anchors.centerIn: parent
            text: verb.label
            color: Theme.textPrimary
            font.pixelSize: Theme.fontMeta
        }
        HoverHandler { id: verbHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: if (verb.enabled) verb.activated() }
    }

    contentItem: Column {
        spacing: 0

        Item {
            id: head
            width: parent.width
            height: 40
            Column {
                anchors.left: parent.left
                anchors.right: headRight.left
                anchors.rightMargin: Theme.spacingSm
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Label {
                    text: "Now playing"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMeta
                }
                Label {
                    width: parent.width
                    text: (pop.rev, deckPopup.title())
                    color: Theme.textPrimary
                    font.pixelSize: 14
                    font.weight: Theme.weightMedium
                    elide: Text.ElideRight
                }
            }
            Row {
                id: headRight
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spacingSm
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: badgeLabel.text !== ""
                    height: 18
                    width: badgeLabel.implicitWidth + 14
                    radius: Theme.radiusXs
                    color: Theme.surfaceVariant
                    Label {
                        id: badgeLabel
                        anchors.centerIn: parent
                        text: (pop.rev, deckPopup.wallpaperType())
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontMicro
                    }
                }
                Item {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 20
                    height: 20
                    Rectangle {
                        anchors.centerIn: parent
                        width: 11; height: 1.5
                        color: closeHover.hovered ? Theme.textPrimary : Theme.textSecondary
                        rotation: 45
                    }
                    Rectangle {
                        anchors.centerIn: parent
                        width: 11; height: 1.5
                        color: closeHover.hovered ? Theme.textPrimary : Theme.textSecondary
                        rotation: -45
                    }
                    HoverHandler { id: closeHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: pop.close() }
                }
            }
        }

        Item {
            id: banner
            width: parent.width
            visible: pop.failedKeys.length > 0
            height: visible ? 40 : 0
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.topMargin: 10
                height: 30
                radius: Theme.radiusSm
                color: Qt.rgba(pop.failColor.r, pop.failColor.g, pop.failColor.b, 0.12)
                border.width: 1
                border.color: Qt.rgba(pop.failColor.r, pop.failColor.g, pop.failColor.b, 0.45)
                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Change Failed"
                    color: Qt.lighter(pop.failColor, 1.45)
                    font.pixelSize: pop.fontBanner
                }
            }
        }

        // --- scrolling body ---------------------------------------------------------------
        //
        // GUTTERS. The scroll viewport is deliberately WIDER than the content it scrolls, for
        // two independent reasons, and its column is inset by the same amounts - so `bodyCol`
        // resolves to exactly `parent.width` and no content ever moves.
        //
        // Left, 6 px: the Global capsule is specced with a negative horizontal margin, bleeding
        // 6 px past the row content on each side. A Flickable that clipped at the content edge
        // cut both of the capsule's vertical border strokes off - Qt draws a Rectangle's border
        // INSIDE its bounds, so those strokes sat entirely outside the clip rect and were
        // discarded, leaving only the top and bottom borders.
        //
        // Right, `rightPadding - 1` px: the same bleed, plus enough reach for the scrollbar to
        // ride out in the card's padding gutter instead of over the text. The viewport's right
        // edge lands on the card's inner border, which is what the bar then measures back from.
        Flickable {
            id: bodyFlick
            objectName: "popupBody"
            x: -pop.bleedLeft
            width: parent.width + pop.bleedLeft + pop.bleedRight
            height: Math.min(bodyCol.implicitHeight, pop.bodyCap)
            contentWidth: width
            contentHeight: bodyCol.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            // Low-profile overlay bar: an ATTACHED ScrollBar is an overlay by construction -
            // it is parented to the Flickable rather than laid out beside it, so it reserves no
            // horizontal space and cannot push content. No track background.
            //
            // Placement: right out at the card edge, in the 16 px padding gutter, clear of every
            // row. The viewport's right edge sits on the card's inner border, so a 2 px right
            // padding puts the handle's right side 3 px in from the card's outer edge and about
            // 13 px clear of the content column - it rides the card, never the text.
            ScrollBar.vertical: ScrollBar {
                id: bodyBar
                policy: ScrollBar.AsNeeded
                rightPadding: 2
                background: Item {}
                contentItem: Rectangle {
                    implicitWidth: 4
                    radius: 2
                    color: Qt.rgba(1, 1, 1, 0.25)
                    opacity: bodyBar.active ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 150 } }
                }
            }

            Column {
                id: bodyCol
                objectName: "popupBodyColumn"
                x: pop.bleedLeft
                width: bodyFlick.width - pop.bleedLeft - pop.bleedRight
                spacing: 0

                Item {
                    width: parent.width
                    height: capsule.height + 12
                    Rectangle {
                        id: capsule
                        objectName: "popupCapsule"
                        x: -6
                        y: 12
                        width: parent.width + 12
                        height: capsuleCol.implicitHeight + 10
                        radius: 7
                        color: Theme.inputWell
                        border.width: 1
                        border.color: Theme.border
                        Column {
                            id: capsuleCol
                            x: 10
                            y: 4
                            width: parent.width - 20
                            spacing: 0

                            PHead { label: "Global"; caption: "Changes the Global Settings" }

                            // Pause animation: the deck pause button's own fact and mechanism.
                            // Read from the same status snapshot the deck reads, written with
                            // the same backend call - one timescale, three doors.
                            PRow {
                                label: "Pause animation"
                                PToggle {
                                    checked: {
                                        var v = Number(pop.engineStatus.speed);
                                        return !isNaN(v) && v === 0;
                                    }
                                    onToggled: function(on) {
                                        var confirmed = backend.setAnimationFrozen(on);
                                        if (confirmed < 0)
                                            deckPopup.reportFailure(["PAUSE"]);
                                    }
                                    ckey: "PAUSE"
                                }
                            }

                            PRow {
                                label: "Speed"
                                Row {
                                    spacing: Theme.spacingSm
                                    // the slider rides POSITION (0..1); the rate is derived
                                    // through the four-zone log map, so the control is linear
                                    // in travel and the value is not
                                    PSlider {
                                        id: speedSlider
                                        objectName: "popupSpeedSlider"
                                        anchors.verticalCenter: parent.verticalCenter
                                        from: 0
                                        to: 1
                                        tickAt: pop.speedDetentPos
                                        value: (pop.rev, pop.posForSpeed(deckPopup.globalSpeed()))
                                        readonly property real speed:
                                            pop.speedDetented(pop.speedForPos(value))
                                        onCommit: function(p) {
                                            var s = pop.speedDetented(pop.speedForPos(p));
                                            // the detent settles the knob on release rather
                                            // than fighting the finger mid-drag
                                            speedSlider.value = pop.posForSpeed(s);
                                            deckPopup.setGlobalSpeed(s);
                                        }
                                    }
                                    PChip {
                                        anchors.verticalCenter: parent.verticalCenter
                                        ckey: "ENGINE_TIMESCALE"
                                        text: pop.speedText(speedSlider.speed)
                                        onEntered: function(t) {
                                            var n = parseFloat(String(t).replace("x", ""));
                                            if (isNaN(n)) { deckPopup.reportFailure(["ENGINE_TIMESCALE"]); return }
                                            // free entry clamps into the band rather than
                                            // refusing - a typed 20 means "as fast as it goes"
                                            deckPopup.setGlobalSpeed(
                                                Math.max(0.1, Math.min(10, n)));
                                        }
                                    }
                                }
                            }

                            PRow {
                                label: "Volume"
                                Row {
                                    spacing: Theme.spacingSm
                                    PSlider {
                                        id: volSlider
                                        anchors.verticalCenter: parent.verticalCenter
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        value: (pop.rev, deckPopup.globalVolume())
                                        onCommit: function(v) { deckPopup.setGlobalVolume(Math.round(v)) }
                                    }
                                    PChip {
                                        anchors.verticalCenter: parent.verticalCenter
                                        ckey: "ENGINE_VOLUME"
                                        text: String(Math.round(volSlider.value))
                                        onEntered: function(t) {
                                            var n = parseInt(t);
                                            if (isNaN(n)) { deckPopup.reportFailure(["ENGINE_VOLUME"]); return }
                                            deckPopup.setGlobalVolume(n);
                                        }
                                    }
                                }
                            }

                            PRow {
                                label: "FPS"
                                PDrop {
                                    compact: true
                                    editable: true
                                    ckey: "ENGINE_FPS"
                                    entries: [
                                        { label: "Auto", value: "" },
                                        { label: "30", value: "30" },
                                        { label: "60", value: "60" },
                                        { label: "120", value: "120" },
                                        { label: "144", value: "144" }
                                    ]
                                    display: {
                                        var v = (pop.rev, deckPopup.globalFps());
                                        return v === "" ? "Auto" : v;
                                    }
                                    onPicked: function(v) { deckPopup.setGlobalFps(v) }
                                    // free integer entry: the bridge is the validator, so a
                                    // non-integer or an out-of-band number raises the banner
                                    // instead of silently falling back to Auto
                                    onEntered: function(t) {
                                        deckPopup.setGlobalFps(
                                            String(t).trim().toLowerCase() === "auto" ? "" : t);
                                    }
                                }
                            }
                        }
                    }
                }

                PHead { label: "This wallpaper" }

                PRow {
                    label: "Scaling"
                    PDrop {
                        ckey: "SCALING"
                        entries: [
                            { label: "Global", value: "" },
                            { label: "Default", value: "default" },
                            { label: "Stretch", value: "stretch" },
                            { label: "Fit", value: "fit" },
                            { label: "Fill", value: "fill" }
                        ]
                        display: {
                            var v = (pop.rev, deckPopup.scalingValue());
                            if (v === "") return "Global";
                            return v.charAt(0).toUpperCase() + v.slice(1);
                        }
                        onPicked: function(v) { deckPopup.setScaling(v) }
                    }
                }

                Item {
                    id: sceneBox
                    width: parent.width
                    // the whole model, in project.json order; the visible set is the subset
                    // whose condition is met
                    readonly property var props: (pop.propRev, deckPopup.sceneProperties())
                    readonly property bool isVideo: (pop.rev, deckPopup.wallpaperType() === "video")
                    implicitHeight: sceneCol.implicitHeight

                    function valueOf(name) {
                        for (var i = 0; i < props.length; i++)
                            if (props[i].name === name)
                                return props[i].value;
                        return undefined;
                    }
                    function recordOf(name) {
                        for (var i = 0; i < props.length; i++)
                            if (props[i].name === name)
                                return props[i];
                        return undefined;
                    }
                    function conditionMet(rec) {
                        var c = rec.condition;
                        if (!c || !c.key)
                            return true;
                        var v = valueOf(c.key);
                        if (v === undefined)
                            return true;
                        return String(v) === String(c.target)
                            || (String(c.target) === "true" && (v === true || String(v) === "true"))
                            || (String(c.target) === "false" && (v === false || String(v) === "false"));
                    }
                    function conditionCaption(rec) {
                        var c = rec.condition;
                        if (!c || !c.key)
                            return "";
                        var owner = recordOf(c.key);
                        var label = owner ? owner.label : c.key;
                        if (owner && owner.kind === "bool")
                            return "while " + label + " is " + (String(c.target) === "false" ? "off" : "on");
                        if (owner && owner.kind === "combo") {
                            for (var i = 0; i < owner.options.length; i++)
                                if (String(owner.options[i].value) === String(c.target))
                                    return "while " + label + " is " + owner.options[i].label;
                        }
                        return "while " + label + " is " + c.target;
                    }

                    Column {
                        id: sceneCol
                        width: parent.width
                        spacing: 0

                        Item {
                            width: parent.width
                            visible: sceneBox.isVideo
                            height: visible ? 34 : 0
                            Label {
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                                text: "Videos don't define scene properties."
                                color: Theme.textTertiary
                                font.pixelSize: Theme.fontMeta
                            }
                        }

                        PHead {
                            visible: !sceneBox.isVideo && sceneBox.props.length > 0
                            height: visible ? 24 : 0
                            label: "Scene properties " + "·" + " " + sceneBox.props.length
                            caption: "authored by this scene"
                        }

                        Repeater {
                            model: sceneBox.isVideo ? [] : sceneBox.props
                            delegate: PRow {
                                id: propRow
                                required property var modelData
                                readonly property bool met: sceneBox.conditionMet(modelData)
                                readonly property bool conditional:
                                    modelData.condition && modelData.condition.key ? true : false
                                // an unmet condition hides the row; the Column drops a hidden
                                // child from its layout on its own, so no height override
                                visible: met
                                label: modelData.label
                                caption: conditional ? sceneBox.conditionCaption(modelData) : ""
                                indented: conditional

                                Loader {
                                    active: propRow.visible
                                    sourceComponent: {
                                        switch (propRow.modelData.kind) {
                                        case "bool": return boolCtl;
                                        case "slider": return sliderCtl;
                                        case "combo": return comboCtl;
                                        case "color": return colorCtl;
                                        default: return textCtl;
                                        }
                                    }
                                    Component {
                                        id: boolCtl
                                        PToggle {
                                            ckey: propRow.modelData.key
                                            checked: propRow.modelData.value === true
                                                     || String(propRow.modelData.value) === "true"
                                            onToggled: function(on) {
                                                deckPopup.setProp(propRow.modelData.name, on ? "true" : "false");
                                            }
                                        }
                                    }
                                    Component {
                                        id: sliderCtl
                                        Row {
                                            spacing: Theme.spacingSm
                                            PSlider {
                                                id: propSlider
                                                anchors.verticalCenter: parent.verticalCenter
                                                from: Number(propRow.modelData.min)
                                                to: Number(propRow.modelData.max)
                                                stepSize: Number(propRow.modelData.step) || 0.01
                                                value: {
                                                    var v = Number(propRow.modelData.value);
                                                    return isNaN(v) ? Number(propRow.modelData.min) || 0 : v;
                                                }
                                                onCommit: function(v) {
                                                    deckPopup.setProp(propRow.modelData.name, String(v));
                                                }
                                            }
                                            PChip {
                                                anchors.verticalCenter: parent.verticalCenter
                                                ckey: propRow.modelData.key
                                                text: propSlider.value.toFixed(2)
                                                onEntered: function(t) {
                                                    var n = parseFloat(t);
                                                    if (isNaN(n)) {
                                                        deckPopup.reportFailure([propRow.modelData.key]);
                                                        return;
                                                    }
                                                    deckPopup.setProp(propRow.modelData.name, String(n));
                                                }
                                            }
                                        }
                                    }
                                    Component {
                                        id: comboCtl
                                        PDrop {
                                            ckey: propRow.modelData.key
                                            entries: propRow.modelData.options
                                            display: {
                                                var opts = propRow.modelData.options;
                                                for (var i = 0; i < opts.length; i++)
                                                    if (String(opts[i].value) === String(propRow.modelData.value))
                                                        return opts[i].label;
                                                return String(propRow.modelData.value);
                                            }
                                            onPicked: function(v) {
                                                deckPopup.setProp(propRow.modelData.name, v);
                                            }
                                        }
                                    }
                                    Component {
                                        id: colorCtl
                                        Row {
                                            spacing: Theme.spacingSm
                                            readonly property var rgb: {
                                                var parts = String(propRow.modelData.value || "1 1 1").split(" ");
                                                var out = [];
                                                for (var i = 0; i < 3; i++) {
                                                    var n = Number(parts[i]);
                                                    out.push(isNaN(n) ? 1 : n);
                                                }
                                                return out;
                                            }
                                            Label {
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: {
                                                    function hx(v) {
                                                        var s = Math.round(Math.max(0, Math.min(1, v)) * 255)
                                                                    .toString(16);
                                                        return s.length < 2 ? "0" + s : s;
                                                    }
                                                    var c = parent.rgb;
                                                    return "#" + hx(c[0]) + hx(c[1]) + hx(c[2]);
                                                }
                                                color: Theme.textTertiary
                                                font.pixelSize: Theme.fontMeta
                                                font.family: Theme.monoFamily
                                            }
                                            Rectangle {
                                                anchors.verticalCenter: parent.verticalCenter
                                                width: 22
                                                height: 22
                                                radius: 5
                                                color: Qt.rgba(parent.rgb[0], parent.rgb[1], parent.rgb[2], 1)
                                                border.width: pop.isFailed(propRow.modelData.key) ? 1.5 : 1
                                                border.color: pop.isFailed(propRow.modelData.key) ? pop.failColor
                                                            : (pop.isMarked(propRow.modelData.key) ? pop.markColor
                                                                                                   : Theme.borderStrong)
                                            }
                                        }
                                    }
                                    Component {
                                        id: textCtl
                                        Rectangle {
                                            width: 132
                                            height: 26
                                            radius: Theme.radiusXs
                                            color: Theme.inputWell
                                            border.width: pop.isFailed(propRow.modelData.key) ? 1.5 : 1
                                            border.color: pop.isFailed(propRow.modelData.key) ? pop.failColor
                                                        : (pop.isMarked(propRow.modelData.key) ? pop.markColor
                                                                                               : Theme.border)
                                            TextInput {
                                                id: propText
                                                anchors.fill: parent
                                                anchors.leftMargin: 8
                                                anchors.rightMargin: 8
                                                verticalAlignment: Text.AlignVCenter
                                                color: Theme.textPrimary
                                                font.pixelSize: pop.fontMono
                                                font.family: Theme.monoFamily
                                                selectByMouse: true
                                                text: String(propRow.modelData.value === undefined
                                                             ? "" : propRow.modelData.value)
                                                Keys.onEscapePressed: {
                                                    propText.text = String(propRow.modelData.value === undefined
                                                                           ? "" : propRow.modelData.value);
                                                    propText.focus = false;
                                                }
                                                onEditingFinished:
                                                    deckPopup.setProp(propRow.modelData.name, propText.text)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                Item { width: parent.width; height: 10 }
                Rectangle { width: parent.width; height: 1; color: Theme.border }
                Item {
                    width: parent.width
                    height: 36
                    Label {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Changes apply live."
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMicro
                    }
                    Row {
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.spacingSm
                        PVerb {
                            label: "Revert changes"
                            enabled: (pop.rev, deckPopup.hasMarks())
                            opacity: enabled ? 1.0 : 0.4
                            onActivated: deckPopup.revertChanges()
                        }
                        PVerb {
                            label: "Load defaults"
                            onActivated: deckPopup.loadDefaults()
                        }
                    }
                }
                Rectangle {
                    width: parent.width
                    height: 30
                    radius: Theme.radiusSm
                    color: doorHover.hovered ? Theme.hoverWash : Theme.surfaceVariant
                    border.width: 1
                    border.color: Theme.border
                    Label {
                        anchors.centerIn: parent
                        text: "Open editor"
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                    }
                    HoverHandler { id: doorHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            var wid = deckPopup.currentWid();
                            pop.close();
                            if (wid !== "")
                                pop.openEditorRequested(wid);
                        }
                    }
                }
            }
        }
    }
}
