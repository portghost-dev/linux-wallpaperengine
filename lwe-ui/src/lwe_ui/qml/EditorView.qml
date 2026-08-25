import QtQuick
import QtQuick.Controls.Basic
import "."

Rectangle {
    id: view
    // named so the offscreen harness can read compactMode without reaching into anonymous
    // children (the same trick the popup's body/capsule carry)
    objectName: "editorView"

    signal closed()

    color: Theme.base

    // the shared 2 s status snapshot (Main pushes it). The editor reads ONE fact from it:
    // which wallpaper the engine is showing, which is the L2 scope gate - an editor open on
    // an idle wallpaper writes its conf and sends the engine nothing.
    property var engineStatus: ({})
    onEngineStatusChanged: editor.syncCurrent(String(view.engineStatus.current || ""))

    property int rev: 0            // bump to re-pull the bridge's scalar reads after a commit
    // `rev` bumps on EVERY edit, so hanging the two big list bindings off it destroyed and
    // recreated every delegate in columns 1 and 2 on each commit. These two bump only when
    // THAT list's content can actually have changed, so a Speed nudge no longer rebuilds the
    // object tree and an object toggle no longer rebuilds the property list (H28).
    property int propRev: 0        // scene-property list (workspace 1)
    property int objRev: 0         // object list (workspace 2)
    property bool isScene: editor.type === "scene"
    readonly property bool isVideo: editor.type === "video"

    readonly property color markColor: Theme.accent
    readonly property color failColor: Theme.danger

    // Qt's font.pixelSize is an INT, so a half-pixel size cannot be assigned as a literal;
    // carried as reals and bound, exactly as Theme.fontMicro already is.
    readonly property real fontRow: 12.5
    readonly property real fontBanner: 11.5
    readonly property real fontMono: 11.5

    function closeEditor() {
        editor.closeEditor();
        view.closed();
    }

    Connections {
        target: editor
        // identity swap - the ONE signal that resets navigation state. Typing a
        // tag or committing a property must never reach this, which is why it is no longer
        // the same signal as the property NOTIFY.
        function onWallpaperChanged() {
            view.rev++; view.propRev++; view.objRev++;
            view.activeWorkspace = 0;
            view.reseedControls();
        }
        // values moved under the controls (revert, defaults, a global commit): re-seed the
        // static controls, but leave every filter, search and collapse state alone.
        function onValuesRefreshed() { view.rev++; view.reseedControls(); }
        function onEdited() { view.rev++ }
        function onPropsEdited() { view.propRev++ }
        function onObjectsEdited() { view.objRev++ }
        function onMetadataChanged() { view.rev++ }
        function onCommitFailed(keys) { view.raiseFailure(keys) }
    }

    // Re-seed the STATIC controls from the bridge. The takeover is a persistent singleton, so
    // Component.onCompleted seeding runs once at app start against an empty bridge and any
    // control the user touches has broken its declarative binding for every later open.
    function reseedControls() {
        titleField.text = editor.title;
        favSwitch.checked = editor.favorite;
    }

    property var failedKeys: []
    function raiseFailure(keys) {
        view.failedKeys = keys;
        failClear.restart();
    }
    Timer {
        id: failClear
        interval: 2500
        onTriggered: view.failedKeys = []
    }
    function isFailed(key) { return key !== "" && view.failedKeys.indexOf(key) >= 0 }
    function isMarked(key) { return (view.rev, key !== "" && editor.isMarked(key)) }

    // --- Speed mapping: four-zone piecewise logarithmic over 0.1x..10x ---------------------
    //
    // Identical to the popup's, deliberately: one fact wears one face everywhere (L8/L-12).
    // Each zone is log-smooth (constant RATIO per pixel, which is what "feels even" for a
    // speed) and the zones meet exactly at their knots, so there is no jump crossing 1.0x,
    // 2.0x or 5.0x.
    //
    //   position   0%     30%    65%    87%   100%
    //   speed     0.1x   1.0x   2.0x   5.0x  10.0x
    readonly property var speedKnotPos:   [0.0, 0.30, 0.65, 0.87, 1.0]
    readonly property var speedKnotValue: [0.1, 1.0,  2.0,  5.0,  10.0]
    readonly property real speedDetent: 1.0
    readonly property real speedDetentBand: 0.02   // snap when within ~2% of 1.0x
    readonly property real speedDetentPos: speedKnotPos[1]

    function speedForPos(p) {
        var pos = Math.max(0, Math.min(1, p));
        for (var i = 0; i < view.speedKnotPos.length - 1; i++) {
            var p0 = view.speedKnotPos[i], p1 = view.speedKnotPos[i + 1];
            if (pos > p1 && i < view.speedKnotPos.length - 2)
                continue;
            var v0 = view.speedKnotValue[i], v1 = view.speedKnotValue[i + 1];
            var t = (p1 === p0) ? 0 : (pos - p0) / (p1 - p0);
            return v0 * Math.pow(v1 / v0, t);       // log-smooth inside the zone
        }
        return view.speedKnotValue[view.speedKnotValue.length - 1];
    }

    function posForSpeed(v) {
        var s = Math.max(view.speedKnotValue[0],
                         Math.min(view.speedKnotValue[view.speedKnotValue.length - 1], Number(v)));
        for (var i = 0; i < view.speedKnotValue.length - 1; i++) {
            var v0 = view.speedKnotValue[i], v1 = view.speedKnotValue[i + 1];
            if (s > v1 && i < view.speedKnotValue.length - 2)
                continue;
            var p0 = view.speedKnotPos[i], p1 = view.speedKnotPos[i + 1];
            return p0 + (p1 - p0) * (Math.log(s / v0) / Math.log(v1 / v0));
        }
        return 1.0;
    }

    function speedDetented(v) {
        return Math.abs(v - view.speedDetent) <= view.speedDetentBand ? view.speedDetent : v;
    }

    // two significant figures, with a decimal kept below 10x so the detent reads 1.0x
    function speedText(v) {
        var r = Number(Number(v).toPrecision(2));
        return ((r < 10 && r === Math.round(r)) ? r.toFixed(1) : String(r)) + "x";
    }

    readonly property real colsWidth: Math.max(0, width - 40)
    readonly property real col3Width: colsWidth - 400 - 1 - 400 - 1
    property bool compactMode: false
    onCol3WidthChanged: {
        if (!view.compactMode && view.col3Width < 330)
            view.compactMode = true;
        else if (view.compactMode && view.col3Width >= 350)
            view.compactMode = false;
    }
    // which workspace the compact switcher is showing. Collapsing preserves marks, scroll
    // positions and every per-workspace navigation state - only visibility changes.
    property int activeWorkspace: 0
    readonly property var workspaceNames: ["Scene Properties", "Object Exclusion", "Tuning"]
    readonly property var workspaceCaptions: [
        "Knobs exposed by the original scene author.",
        "Disables individual scene objects.",
        "Per-wallpaper and advanced knobs offered by the engine."
    ]

    function workspaceVisible(i) { return !view.compactMode || view.activeWorkspace === i }

    readonly property real barGutter: 16
    function contentInset(againstDivider) {
        return view.barGutter;
    }

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
                font.pixelSize: view.fontRow
                elide: Text.ElideRight
            }
            Label {
                width: parent.width
                // no `height: visible ? implicitHeight : 0` here: on an eliding Label that
                // binding is a height/implicitHeight loop, and the enclosing Column already
                // drops an invisible child from its layout entirely
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

    component PRule: Item {
        id: prule
        property string label: ""
        default property alias slot: ruleSlot.data
        width: parent ? parent.width : 0
        height: 26
        Label {
            id: ruleLabel
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: prule.label
            color: Theme.textSecondary
            font.pixelSize: Theme.fontMeta
            font.weight: Theme.weightMedium
        }
        Rectangle {
            anchors.left: ruleLabel.right
            anchors.leftMargin: 8
            anchors.right: ruleSlot.childrenRect.width > 0 ? ruleSlot.left : parent.right
            anchors.rightMargin: ruleSlot.childrenRect.width > 0 ? 10 : 0
            anchors.verticalCenter: parent.verticalCenter
            height: 1
            color: Theme.border
        }
        Item {
            id: ruleSlot
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: childrenRect.width
            height: childrenRect.height
        }
    }

    component PSlider: Slider {
        id: sld
        property string ckey: ""
        property real tickAt: -1        // 0..1 position of a hash tick; -1 draws none
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
        property var entries: []            // [] = no menu, plain free-entry chip
        signal entered(string text)
        signal picked(string value)
        height: 22
        width: Math.max(46, chipLabel.implicitWidth + 16)
        radius: 5
        color: Theme.surface
        border.width: view.isFailed(ckey) ? 1.5 : 1
        border.color: view.isFailed(ckey) ? view.failColor
                    : (view.isMarked(ckey) ? view.markColor : Theme.border)
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
        HoverHandler { cursorShape: chip.entries.length > 0 ? Qt.PointingHandCursor : Qt.IBeamCursor }
        TapHandler {
            onTapped: {
                if (chip.entries.length > 0) {
                    if (chipMenu.visible) chipMenu.close();
                    else if (!chipMenu.justClosed) chipMenu.open();
                    return;
                }
                chipEdit.text = chip.text;
                chip.editing = true;
                chipEdit.forceActiveFocus();
                chipEdit.selectAll();
            }
        }
        Menu {
            id: chipMenu
            y: chip.height + 2
            property bool justClosed: false
            onClosed: { justClosed = true; chipGuard.restart() }
            Timer { id: chipGuard; interval: 150; onTriggered: chipMenu.justClosed = false }
            background: Rectangle {
                implicitWidth: 150
                color: Theme.surfaceVariant
                radius: Theme.radiusSm
                border.width: 1
                border.color: Theme.borderStrong
            }
            Repeater {
                model: chip.entries
                delegate: ThemedMenuItem {
                    required property var modelData
                    text: modelData.label
                    onTriggered: {
                        if (String(modelData.value) === "@entry") {
                            chipEdit.text = chip.text;
                            chip.editing = true;
                            chipEdit.forceActiveFocus();
                            chipEdit.selectAll();
                        } else {
                            chip.picked(String(modelData.value));
                        }
                    }
                }
            }
        }
    }

    // dropdown: the caller fills `entries` with {label, value} and handles picked()
    component PDrop: Rectangle {
        id: drop
        property string ckey: ""
        property var entries: []
        property string display: ""
        property bool compact: false
        // FPS: "menu ... plus free integer entry". The caret zone opens the menu,
        // the label zone takes typing - the standard editable-dropdown split, which adds the
        // entry affordance without a second control or one word of new copy (L4, L7).
        property bool editable: false
        property bool editing: false
        signal picked(string value)
        signal entered(string text)
        height: compact ? 24 : 26
        width: compact ? 78 : Math.max(96, dropLabel.implicitWidth + (compact ? 26 : 32))
        radius: compact ? 5 : 6
        color: Theme.surface
        border.width: view.isFailed(ckey) ? 1.5 : 1
        border.color: view.isFailed(ckey) ? view.failColor
                    : (view.isMarked(ckey) ? view.markColor : Theme.border)
        Label {
            id: dropLabel
            visible: !drop.editing
            anchors.left: parent.left
            anchors.leftMargin: drop.compact ? 8 : 10
            anchors.right: caret.left
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
            visible: view.isFailed(tgl.ckey) || view.isMarked(tgl.ckey)
            border.width: view.isFailed(tgl.ckey) ? 1.5 : 1
            border.color: view.isFailed(tgl.ckey) ? view.failColor : view.markColor
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
        color: verbHover.hovered && verb.enabled ? Theme.hoverWash : "transparent"
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

    component PScrollBar: ScrollBar {
        id: sbar
        policy: ScrollBar.AsNeeded
        rightPadding: 3
        background: Item {}
        contentItem: Rectangle {
            implicitWidth: 4
            radius: 2
            color: Qt.rgba(1, 1, 1, 0.25)
            opacity: sbar.active ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 150 } }
        }
    }

    Rectangle {
        id: hdr
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 48
        color: Theme.base

        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }

        Row {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: view.compactMode ? 16 : 20
            spacing: Theme.spacingMd

            // back to the library. Leaving is assent - nothing is discarded on the way out.
            Row {
                id: hdrBack
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spacingXs
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 6; height: 6; rotation: 45
                    color: "transparent"
                    border.width: 0
                    Rectangle { anchors.left: parent.left; width: 1.5; height: parent.height; color: Theme.textSecondary }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1.5; color: Theme.textSecondary }
                }
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Library"
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontControl
                }
                TapHandler { onTapped: view.closeEditor() }
            }
            Rectangle {
                width: 36; height: 22; radius: Theme.radiusXs
                anchors.verticalCenter: parent.verticalCenter
                color: Theme.surfaceVariant
                clip: true
                Image { anchors.fill: parent; source: editor.previewUrl; fillMode: Image.PreserveAspectCrop; sourceSize.width: Theme.previewCap }
            }
            Label {
                anchors.verticalCenter: parent.verticalCenter
                text: editor.title
                color: Theme.textPrimary
                font.pixelSize: Theme.fontNav
                font.weight: Theme.weightMedium
                elide: Text.ElideRight
                width: Math.max(48, Math.min(implicitWidth,
                    hdr.width - hdrVerbs.width - hdrBack.width - 36 - hdrPill.width
                   - hdrChip.width - Theme.spacingMd * 4
                   - (view.compactMode ? 32 : 40) - 12))
            }
            Rectangle {
                id: hdrPill
                anchors.verticalCenter: parent.verticalCenter
                radius: Theme.radiusXs
                color: Theme.surfaceVariant
                width: typeBadge.implicitWidth + Theme.spacingSm + Theme.spacingXs
                height: 18
                Label { id: typeBadge; anchors.centerIn: parent; text: editor.type
                        color: Theme.textMutedBody; font.pixelSize: Theme.fontMeta }
            }
            Rectangle {
                id: hdrChip
                anchors.verticalCenter: parent.verticalCenter
                radius: Theme.radiusXs
                color: idHover.hovered ? Theme.hoverWash : "transparent"
                border.width: 1
                border.color: Theme.border
                height: 22
                // CAPPED. Steam ids are ~10 digits, but an advanced import takes its id from
                // the FOLDER NAME, which can be arbitrarily long - uncapped, one import would
                // stretch this chip across the header. The label elides; the CLIPBOARD still
                // gets the full id, so a truncated display never yields a truncated copy.
                width: Math.min(idRow.implicitWidth + Theme.spacingSm * 2, 190)
                Row {
                    id: idRow
                    anchors.centerIn: parent
                    spacing: Theme.spacingXs
                    Label {
                        id: idChip
                        anchors.verticalCenter: parent.verticalCenter
                        text: view.compactMode ? "ID" : editor.wallpaperId
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontMeta
                        font.family: Theme.monoFamily
                        elide: Text.ElideMiddle       // keep both ends: the name AND its -adv-N
                        width: Math.min(implicitWidth, 150)
                    }
                    Item {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 10; height: 10
                        Rectangle { x: 0; y: 0; width: 7; height: 7; radius: 2; color: "transparent"
                                    border.width: 1; border.color: Theme.textTertiary }
                        Rectangle { x: 3; y: 3; width: 7; height: 7; radius: 2; color: Theme.base
                                    border.width: 1; border.color: Theme.textTertiary }
                    }
                }
                HoverHandler { id: idHover }
                // a REAL clipboard copy; the "copied" flash only fires when the copy succeeds
                // (headless/no-GUI returns false, so the flash stays honest). Copies
                // editor.wallpaperId, never idChip.text - the label may be elided, showing the
                // literal "ID" at compact, or mid-flash at the time.
                TapHandler { onTapped: {
                    if (editor.copyToClipboard(editor.wallpaperId)) { idChip.text = "copied"; copyReset.restart(); }
                } }
                Timer { id: copyReset; interval: 900
                        onTriggered: idChip.text = Qt.binding(function() {
                            return view.compactMode ? "ID" : editor.wallpaperId; }) }
            }
        }

        Row {
            id: hdrVerbs
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: parent.right
            anchors.rightMargin: view.compactMode ? 16 : 20
            spacing: Theme.spacingSm

            PVerb {
                label: "Revert changes"
                // disabled with nothing marked, AND disabled while the snapshot is invalid -
                // a revert that cannot restore must not run (F16 part 2)
                enabled: (view.rev, editor.canRevert())
                opacity: enabled ? 1.0 : 0.4
                onActivated: editor.revertChanges()
            }
            PVerb {
                label: "Load defaults"
                onActivated: editor.loadDefaults()
            }
        }
    }

    Item {
        id: banner
        anchors.top: hdr.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: view.compactMode ? 16 : 20
        anchors.rightMargin: view.compactMode ? 16 : 20
        visible: view.failedKeys.length > 0
        height: visible ? 40 : 0
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.topMargin: 10
            height: 30
            radius: Theme.radiusSm
            color: Qt.rgba(view.failColor.r, view.failColor.g, view.failColor.b, 0.12)
            border.width: 1
            border.color: Qt.rgba(view.failColor.r, view.failColor.g, view.failColor.b, 0.45)
            Label {
                anchors.left: parent.left
                anchors.leftMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                text: "Change Failed"
                color: Qt.lighter(view.failColor, 1.45)
                font.pixelSize: view.fontBanner
            }
        }
    }

    Column {
        id: compactChrome
        visible: view.compactMode
        height: visible ? implicitHeight : 0
        anchors.top: banner.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.topMargin: visible ? 10 : 0
        spacing: Theme.spacingXs

        // 29c: the switcher on the left, and - while Object Exclusion is the visible
        // workspace - that workspace's type filter and master bulk toggle on the right of
        // the SAME row. They are the panel's own controls, loaded from it rather than
        // rebuilt here, so there is one filter state and one derived toggle, not two.
        Item {
            width: parent.width
            height: Math.max(wsSeg.height, objHdrControls.height)
            SegmentControl {
                id: wsSeg
                sizeClass: "h24"
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                model: view.workspaceNames
                currentIndex: view.activeWorkspace
                onActivated: function(i) { view.activeWorkspace = i; }
            }
            Loader {
                id: objHdrControls
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                active: view.compactMode && view.activeWorkspace === 1
                sourceComponent: active ? objPanel.headerControls : null
            }
        }
        Label {
            width: parent.width
            text: view.workspaceCaptions[view.activeWorkspace]
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMicro
            elide: Text.ElideRight
        }
    }

    // --- workspaces --------------------------------------------------------------------------
    // The same three column Items serve both modes: collapsing changes visibility and width,
    // never lifecycle, so marks, scroll positions, filters, searches, tree mode and the group
    // collapse map all survive the transition instead of being rebuilt.
    Row {
        id: cols
        anchors.top: view.compactMode ? compactChrome.bottom : banner.bottom
        anchors.topMargin: 12
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.leftMargin: view.compactMode ? 16 : 20
        anchors.right: parent.right
        anchors.rightMargin: view.compactMode ? 16 : 20

        Item {
            id: col1
            visible: view.workspaceVisible(0)
            width: visible ? (view.compactMode ? cols.width : 400) : 0
            height: parent.height

            Column {
                anchors.fill: parent
                spacing: Theme.spacingSm
                // the scroll viewport below spans the FULL column; everything that is not the
                // viewport carries the same content inset so the two stay flush (L-14)
                readonly property real inset: view.contentInset(true)

                Column {
                    width: parent.width - parent.inset
                    visible: !view.compactMode
                    height: visible ? implicitHeight : 0
                    spacing: 2
                    Label {
                        text: "Scene Properties · " + propCol.props.length
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                    }
                    Label {
                        text: view.workspaceCaptions[0]
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMicro
                    }
                }
                TextField {
                    id: propFilter
                    width: parent.width - parent.inset
                    height: 24
                    placeholderText: "Filter properties"
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontMeta
                    font.family: Theme.monoFamily
                    background: Rectangle { color: Theme.inputWell; radius: Theme.radiusXs
                        border.width: 1; border.color: parent.activeFocus ? Theme.borderStrong : Theme.border }
                }
                Flickable {
                    id: propFlick
                    // full column width: the bar rides the COLUMN's right edge, not the
                    // content's, and the column below it is what carries the inset
                    width: parent.width
                    height: Math.max(0, parent.height - y)
                    contentWidth: width
                    contentHeight: propCol.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: PScrollBar {}

                    Column {
                        id: propCol
                        width: propFlick.width - view.contentInset(true)
                        spacing: 0
                        // The delegates bind to editor.scenePropertyModel - a STABLE model
                        // whose identity never changes, so a commit touches ONE row through
                        // dataChanged instead of destroying and recreating every delegate.
                        // `props` is the condition evaluator's LOOKUP
                        // TABLE only; nothing's lifecycle hangs off it.
                        readonly property var props: (view.propRev, editor.sceneProperties())

                        function labelOf(name) {
                            var r = recordOf(name);
                            return r ? String(r.label || r.name) : String(name);
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
                            var owner = recordOf(c.key);
                            if (owner === undefined)
                                return true;
                            var v = owner.value;
                            return String(v) === String(c.target)
                                || (String(c.target) === "true" && (v === true || String(v) === "true"))
                                || (String(c.target) === "false" && (v === false || String(v) === "false"));
                        }
                        // state the ACTUAL condition target, never a hardcoded "is on"
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
                        function matchesFilter(md) {
                            if (propFilter.text === "") return true;
                            var q = propFilter.text.toLowerCase();
                            var hay = (String(md.label || md.name) + " " + String(md.name) + " "
                                       + String(md.group || "")).toLowerCase();
                            return hay.indexOf(q) >= 0;
                        }

                        Item {
                            width: parent.width
                            visible: view.isVideo
                            height: visible ? 34 : 0
                            Label {
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                                text: "Videos don't define scene properties."
                                color: Theme.textTertiary
                                font.pixelSize: Theme.fontMeta
                            }
                        }

                        Repeater {
                            model: view.isVideo ? null : editor.scenePropertyModel
                            delegate: PRow {
                                id: propRow
                                required property string name
                                required property string ckey
                                required property string kind
                                required property var value
                                required property var pmin
                                required property var pmax
                                required property var pstep
                                required property var options
                                required property var condition
                                readonly property var modelData: ({
                                    name: propRow.name, key: propRow.ckey, kind: propRow.kind,
                                    label: propRow.label, value: propRow.value,
                                    min: propRow.pmin, max: propRow.pmax, step: propRow.pstep,
                                    options: propRow.options, condition: propRow.condition
                                })
                                readonly property bool met: propCol.conditionMet(modelData)
                                readonly property bool conditional:
                                    condition && condition.key ? true : false
                                // an unmet condition hides the row; the Column drops a hidden
                                // child from its layout on its own, so no height override
                                visible: met && propCol.matchesFilter(modelData)
                                label: propCol.labelOf(propRow.name)
                                caption: conditional ? propCol.conditionCaption(modelData) : ""
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
                                                editor.setProp(propRow.modelData.name, on ? "true" : "false");
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
                                                    editor.setProp(propRow.modelData.name, String(v));
                                                }
                                            }
                                            PChip {
                                                anchors.verticalCenter: parent.verticalCenter
                                                ckey: propRow.modelData.key
                                                text: propSlider.value.toFixed(2)
                                                onEntered: function(t) {
                                                    var n = parseFloat(t);
                                                    if (isNaN(n)) {
                                                        editor.reportFailure([propRow.modelData.key]);
                                                        return;
                                                    }
                                                    editor.setProp(propRow.modelData.name, String(n));
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
                                                editor.setProp(propRow.modelData.name, v);
                                            }
                                        }
                                    }
                                    Component {
                                        id: colorCtl
                                        Row {
                                            spacing: Theme.spacingSm
                                            readonly property string storedHex:
                                                editor.colorHex(String(propRow.modelData.value || "1 1 1"))
                                            Rectangle {
                                                id: hexWell
                                                anchors.verticalCenter: parent.verticalCenter
                                                width: 92
                                                height: 26
                                                radius: Theme.radiusXs
                                                color: Theme.inputWell
                                                border.width: view.isFailed(propRow.modelData.key) ? 1.5 : 1
                                                border.color: view.isFailed(propRow.modelData.key) ? view.failColor
                                                            : (view.isMarked(propRow.modelData.key) ? view.markColor
                                                                                                    : Theme.border)
                                                TextInput {
                                                    id: hexInput
                                                    anchors.fill: parent
                                                    anchors.leftMargin: 8
                                                    anchors.rightMargin: 8
                                                    verticalAlignment: Text.AlignVCenter
                                                    color: Theme.textPrimary
                                                    font.pixelSize: view.fontMono
                                                    font.family: Theme.monoFamily
                                                    selectByMouse: true
                                                    text: parent.parent.storedHex
                                                    Keys.onEscapePressed: {
                                                        hexInput.text = Qt.binding(function() {
                                                            return hexWell.parent.storedHex; });
                                                        hexInput.focus = false;
                                                    }
                                                    onEditingFinished: {
                                                        if (hexInput.text === hexWell.parent.storedHex)
                                                            return;
                                                        // a refusal leaves the conf alone, so
                                                        // put the field back on store truth
                                                        if (!editor.setPropColor(propRow.modelData.name,
                                                                                 hexInput.text)) {
                                                            hexInput.text = Qt.binding(function() {
                                                                return hexWell.parent.storedHex; });
                                                        }
                                                    }
                                                }
                                            }
                                            Rectangle {
                                                anchors.verticalCenter: parent.verticalCenter
                                                width: 22
                                                height: 22
                                                radius: 5
                                                color: parent.storedHex
                                                border.width: 1
                                                border.color: Theme.borderStrong
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
                                            border.width: view.isFailed(propRow.modelData.key) ? 1.5 : 1
                                            border.color: view.isFailed(propRow.modelData.key) ? view.failColor
                                                        : (view.isMarked(propRow.modelData.key) ? view.markColor
                                                                                                : Theme.border)
                                            TextInput {
                                                id: propText
                                                anchors.fill: parent
                                                anchors.leftMargin: 8
                                                anchors.rightMargin: 8
                                                verticalAlignment: Text.AlignVCenter
                                                color: Theme.textPrimary
                                                font.pixelSize: view.fontMono
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
                                                    editor.setProp(propRow.modelData.name, propText.text)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Label {
                            visible: !view.isVideo && propCol.props.length === 0
                                     && (view.rev, editor.propertiesReadable())
                            height: visible ? implicitHeight + Theme.spacingSm : 0
                            text: "This wallpaper exposes no properties."
                            color: Theme.textTertiary
                            font.pixelSize: Theme.fontMeta
                        }
                        // an unreadable project.json is NOT a scene without properties, and
                        // showing the same empty state for both made a broken read invisible.
                        Label {
                            visible: (view.rev, !editor.propertiesReadable())
                            height: visible ? implicitHeight + Theme.spacingSm : 0
                            width: propCol.width
                            text: "This wallpaper's project file could not be read."
                            color: Qt.lighter(view.failColor, 1.45)
                            font.pixelSize: Theme.fontMeta
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }
        Rectangle { visible: !view.compactMode; width: visible ? 1 : 0; height: parent.height
                    color: Theme.border }

        Item {
            id: col2
            visible: view.workspaceVisible(1)
            width: visible ? (view.compactMode ? cols.width : 400) : 0
            height: parent.height

            ObjectsPanel {
                id: objPanel
                anchors.fill: parent
                anchors.leftMargin: view.compactMode ? 0 : 22   // left gutter toward the divider
                // NO right margin: the panel spans the column so its list's overlay bar can
                // ride the column's own right edge. `barGutter` is the content's reserve.
                rev: view.objRev
                barGutter: view.contentInset(true)
                compactMode: view.compactMode
                showHeading: !view.compactMode
                heading: view.workspaceNames[1]
                caption: view.workspaceCaptions[1]
            }
        }
        Rectangle { visible: !view.compactMode; width: visible ? 1 : 0; height: parent.height
                    color: Theme.border }

        Item {
            id: col3
            visible: view.workspaceVisible(2)
            width: visible ? (view.compactMode ? cols.width : Math.max(0, view.col3Width)) : 0
            height: parent.height

            Flickable {
                anchors.fill: parent
                anchors.leftMargin: view.compactMode ? 0 : 22
                contentWidth: width
                contentHeight: tuneCol.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: PScrollBar {}

                Column {
                    id: tuneCol
                    // column 3 has no divider on its right, so it reserves the bar's own 16
                    width: parent.width - view.contentInset(false)
                    spacing: 0

                    Column {
                        width: parent.width
                        visible: !view.compactMode
                        height: visible ? implicitHeight + Theme.spacingSm : 0
                        spacing: 2
                        Label {
                            text: view.workspaceNames[2]
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                        }
                        Label {
                            text: view.workspaceCaptions[2]
                            color: Theme.textTertiary
                            font.pixelSize: Theme.fontMicro
                        }
                    }

                    // --- Global capsule ------------------------------------
                    // Foreign scope, so it wears the capsule rather than a header rule
                    // (C-11). These rows are DIRECT global knobs: they commit outright, they
                    // never wear the mark, and they are never in the revert set (L5, L-17).
                    // The popup's -6 px side bleed is dropped: it exists to reach the card's
                    // padding, and a column has no analog.
                    Item {
                        width: parent.width
                        height: capsule.height + 12
                        Rectangle {
                            id: capsule
                            y: 12
                            width: parent.width
                            height: capsuleCol.implicitHeight + 10
                            radius: 7
                            color: Theme.surface
                            border.width: 1
                            border.color: Theme.border
                            Column {
                                id: capsuleCol
                                x: 10
                                y: 4
                                width: parent.width - 20
                                spacing: 0

                                PHead { label: "Global"; caption: "Changes the Global Settings" }

                                // Pause animation: the deck pause button's own fact and
                                // mechanism. Read from the same status snapshot the deck
                                // reads, written with the same backend call - one timescale,
                                // several doors, one state.
                                PRow {
                                    label: "Pause animation"
                                    PToggle {
                                        ckey: "PAUSE"
                                        checked: {
                                            var v = Number(view.engineStatus.speed);
                                            return !isNaN(v) && v === 0;
                                        }
                                        onToggled: function(on) {
                                            var confirmed = backend.setAnimationFrozen(on);
                                            if (confirmed < 0)
                                                editor.reportFailure(["PAUSE"]);
                                        }
                                    }
                                }

                                PRow {
                                    label: "Speed"
                                    Row {
                                        spacing: Theme.spacingSm
                                        // the slider rides POSITION (0..1); the rate is
                                        // derived through the four-zone log map, so the
                                        // control is linear in travel and the value is not
                                        PSlider {
                                            id: gSpeedSlider
                                            anchors.verticalCenter: parent.verticalCenter
                                            from: 0
                                            to: 1
                                            tickAt: view.speedDetentPos
                                            value: (view.rev, view.posForSpeed(editor.globalSpeed()))
                                            readonly property real speed:
                                                view.speedDetented(view.speedForPos(value))
                                            onCommit: function(p) {
                                                // no imperative value write: it would break the
                                                // binding; the refresh re-binds to the detented value
                                                editor.setGlobalSpeed(view.speedDetented(view.speedForPos(p)));
                                            }
                                        }
                                        PChip {
                                            anchors.verticalCenter: parent.verticalCenter
                                            ckey: "ENGINE_TIMESCALE"
                                            text: view.speedText(gSpeedSlider.speed)
                                            onEntered: function(t) {
                                                var n = parseFloat(String(t).replace("x", ""));
                                                if (isNaN(n)) { editor.reportFailure(["ENGINE_TIMESCALE"]); return }
                                                // free entry clamps into the band rather than
                                                // refusing - a typed 20 means "as fast as it goes"
                                                editor.setGlobalSpeed(Math.max(0.1, Math.min(10, n)));
                                            }
                                        }
                                    }
                                }

                                PRow {
                                    label: "Volume"
                                    Row {
                                        spacing: Theme.spacingSm
                                        PSlider {
                                            id: gVolSlider
                                            anchors.verticalCenter: parent.verticalCenter
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            value: (view.rev, editor.globalVolume())
                                            onCommit: function(v) { editor.setGlobalVolume(Math.round(v)) }
                                        }
                                        PChip {
                                            anchors.verticalCenter: parent.verticalCenter
                                            ckey: "ENGINE_VOLUME"
                                            text: String(Math.round(gVolSlider.value))
                                            onEntered: function(t) {
                                                var n = parseInt(t);
                                                if (isNaN(n)) { editor.reportFailure(["ENGINE_VOLUME"]); return }
                                                editor.setGlobalVolume(n);
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
                                            var v = (view.rev, editor.globalFps());
                                            return v === "" ? "Auto" : v;
                                        }
                                        onPicked: function(v) { editor.setGlobalFps(v) }
                                        // free integer entry: the bridge is the validator, so
                                        // a non-integer or an out-of-band number raises the
                                        // banner instead of silently falling back to Auto
                                        onEntered: function(t) {
                                            editor.setGlobalFps(
                                                String(t).trim().toLowerCase() === "auto" ? "" : t);
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // --- per-wallpaper tier --------------------------------
                    // Override grammar (L4): `Global` is an entry INSIDE each control, never
                    // a second control and never a morphing pill. Choosing it deletes the
                    // key, which is what makes SCALING=default and zero-values expressible
                    // as real overrides. Rows exist only for keys with a real per-wallpaper
                    // store (L-17).
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
                                var v = (view.rev, editor.scalingValue());
                                if (v === "") return "Global";
                                return v.charAt(0).toUpperCase() + v.slice(1);
                            }
                            onPicked: function(v) { editor.setScalingValue(v) }
                        }
                    }

                    PRow {
                        label: "Speed"
                        Row {
                            spacing: Theme.spacingSm
                            PSlider {
                                id: wpSpeedSlider
                                anchors.verticalCenter: parent.verticalCenter
                                from: 0
                                to: 1
                                tickAt: view.speedDetentPos
                                value: {
                                    var raw = (view.rev, editor.speedValue());
                                    return view.posForSpeed(raw === "" ? 1.0 : Number(raw));
                                }
                                readonly property real speed:
                                    view.speedDetented(view.speedForPos(value))
                                onCommit: function(p) {
                                    // never assign value here: an imperative write would break
                                    // the declarative binding and orphan the slider from Global
                                    editor.setSpeedValue(view.speedDetented(view.speedForPos(p)));
                                }
                            }
                            PChip {
                                anchors.verticalCenter: parent.verticalCenter
                                ckey: "SPEED"
                                // an inheriting row reads Global; touching the slider makes
                                // it custom, and the menu is the way back
                                text: (view.rev, editor.speedValue()) === ""
                                      ? "Global" : view.speedText(wpSpeedSlider.speed)
                                entries: [
                                    { label: "Global (" + (view.rev, editor.globalDefaultFor("SPEED")) + ")",
                                      value: "" },
                                    { label: "Enter a value", value: "@entry" }
                                ]
                                onPicked: function(v) { if (v === "") editor.clearOverride("speed") }
                                onEntered: function(t) {
                                    var n = parseFloat(String(t).replace("x", ""));
                                    if (isNaN(n)) { editor.reportFailure(["SPEED"]); return }
                                    editor.setSpeedValue(Math.max(0.1, Math.min(10, n)));
                                }
                            }
                        }
                    }

                    PRow {
                        label: "Volume"
                        Row {
                            spacing: Theme.spacingSm
                            PSlider {
                                id: wpVolSlider
                                anchors.verticalCenter: parent.verticalCenter
                                from: 0
                                to: 100
                                stepSize: 1
                                value: {
                                    var raw = (view.rev, editor.volumeValue());
                                    return raw === "" ? editor.globalVolume() : Number(raw);
                                }
                                onCommit: function(v) { editor.setVolumeValue(Math.round(v)) }
                            }
                            PChip {
                                anchors.verticalCenter: parent.verticalCenter
                                ckey: "VOLUME"
                                text: (view.rev, editor.volumeValue()) === ""
                                      ? "Global" : String(Math.round(wpVolSlider.value))
                                entries: [
                                    { label: "Global (" + (view.rev, editor.globalDefaultFor("VOLUME")) + ")",
                                      value: "" },
                                    { label: "Enter a value", value: "@entry" }
                                ]
                                onPicked: function(v) { if (v === "") editor.clearOverride("volume") }
                                onEntered: function(t) {
                                    var n = parseInt(t);
                                    if (isNaN(n)) { editor.reportFailure(["VOLUME"]); return }
                                    editor.setVolumeValue(n);
                                }
                            }
                        }
                    }

                    Repeater {
                        model: [
                            { label: "Audio-reactive", key: "AUDIO_REACTIVE" },
                            { label: "Mouse input",    key: "MOUSE" },
                            { label: "Auto-mute",      key: "AUTOMUTE" }
                        ]
                        delegate: PRow {
                            id: boolRow
                            required property var modelData
                            label: modelData.label
                            PDrop {
                                ckey: boolRow.modelData.key
                                entries: [
                                    { label: "Global (" + (view.rev, editor.globalDefaultFor(boolRow.modelData.key)) + ")",
                                      value: "" },
                                    { label: "On", value: "true" },
                                    { label: "Off", value: "false" }
                                ]
                                display: {
                                    var raw = (view.rev,
                                        boolRow.modelData.key === "AUDIO_REACTIVE" ? editor.audioReactiveValue()
                                      : boolRow.modelData.key === "MOUSE" ? editor.mouseValue()
                                      : editor.automuteValue());
                                    if (raw === "") return "Global";
                                    return raw === "true" ? "On" : "Off";
                                }
                                onPicked: function(v) {
                                    editor.setBoolOverride(boolRow.modelData.key, v);
                                }
                            }
                        }
                    }

                    // --- Audio response ------------------------------------
                    // The three DRAWN rows, wired to the three real engine dials behind
                    // them. All GLOBAL: no per-wallpaper store exists for any of them, so
                    // they commit outright, wear no mark and stay out of the revert set.
                    // Seeded from the engine's live status, never from a source default.
                    //
                    // Each slider rides the 0..1 QUALITY its label names - dragging right
                    // always increases that quality - and the bridge maps it back to the
                    // dial, which for all three runs the other way (see AUDIO_DIALS). The
                    // chip shows the quality to two decimals, which is what 29a draws.
                    Item { width: parent.width; height: Theme.spacingMd }
                    PRule {
                        label: "Audio response"
                        PDrop {
                            ckey: "AUDIO_MODE"
                            entries: [
                                { label: "Global", value: "global" },
                                { label: "Custom", value: "custom" }
                            ]
                            display: (view.rev, editor.audioMode()) === "custom" ? "Custom" : "Global"
                            onPicked: function(v) { editor.setAudioMode(v) }
                        }
                    }
                    Repeater {
                        model: (view.rev, editor.audioDials())
                        delegate: PRow {
                            id: dialRow
                            required property var modelData
                            label: modelData.label
                            visible: (view.rev, editor.audioMode()) === "custom"
                            Row {
                                spacing: Theme.spacingSm
                                PSlider {
                                    id: dialSlider
                                    anchors.verticalCenter: parent.verticalCenter
                                    from: 0
                                    to: 1
                                    value: dialRow.modelData.quality
                                    onCommit: function(q) {
                                        editor.setAudioDial(dialRow.modelData.key, q);
                                    }
                                }
                                PChip {
                                    anchors.verticalCenter: parent.verticalCenter
                                    ckey: dialRow.modelData.key
                                    text: dialSlider.value.toFixed(2)
                                    onEntered: function(t) {
                                        var n = parseFloat(t);
                                        if (isNaN(n)) { editor.reportFailure([dialRow.modelData.key]); return }
                                        editor.setAudioDial(dialRow.modelData.key,
                                                            Math.max(0, Math.min(1, n)));
                                    }
                                }
                            }
                        }
                    }

                    // --- Color correction -----------------------------------
                    // The master is a two-entry menu: `Custom` is a DISPLAYED state entered by
                    // touching a slider, never a selectable entry (L-4). CC stays materialized
                    // as the effective-numbers cache in every mode, so the show path never has
                    // to parse scene JSON (L-5).
                    Item { width: parent.width; height: Theme.spacingMd }
                    PRule {
                        label: "Color correction"
                        PDrop {
                            ckey: "CC_MODE"
                            entries: [
                                { label: "None", value: "none" },
                                { label: "Custom", value: "custom" }
                            ]
                            display: {
                                var m = (view.rev, editor.ccMode());
                                return m === "none" ? "None" : "Custom";
                            }
                            onPicked: function(v) { editor.setCcMode(v) }
                        }
                    }
                    Repeater {
                        model: [
                            { label: "Brightness", idx: 0, lo: 0,  hi: 2, detent: 1 },
                            { label: "Contrast",   idx: 1, lo: 0,  hi: 2, detent: 1 },
                            { label: "Saturation", idx: 2, lo: 0,  hi: 2, detent: 1 },
                            { label: "Hue",        idx: 3, lo: -1, hi: 1, detent: 0 }
                        ]
                        delegate: PRow {
                            id: ccRow
                            required property var modelData
                            label: modelData.label
                            visible: (view.rev, editor.ccMode()) !== "none"
                            Row {
                                spacing: Theme.spacingSm
                                PSlider {
                                    id: ccSlider
                                    anchors.verticalCenter: parent.verticalCenter
                                    from: ccRow.modelData.lo
                                    to: ccRow.modelData.hi
                                    stepSize: 0.01
                                    value: (view.rev, editor.ccChannels()[ccRow.modelData.idx])
                                    tickAt: (ccRow.modelData.detent - ccRow.modelData.lo)
                                            / (ccRow.modelData.hi - ccRow.modelData.lo)
                                    onCommit: function(v) {
                                        var d = ccRow.modelData.detent;
                                        var out = Math.abs(v - d) <= 0.03 ? d : v;
                                        ccSlider.value = out;
                                        editor.setCcChannel(ccRow.modelData.idx, out);
                                    }
                                }
                                PChip {
                                    anchors.verticalCenter: parent.verticalCenter
                                    ckey: "CC"
                                    text: ccSlider.value.toFixed(2)
                                    onEntered: function(t) {
                                        var n = parseFloat(t);
                                        if (isNaN(n)) { editor.reportFailure(["CC"]); return }
                                        editor.setCcChannel(ccRow.modelData.idx, n);
                                    }
                                }
                            }
                        }
                    }

                    // --- Metadata ----------------------------------------------
                    // Instant-write to meta.json / tags.csv, never to wp/<id>.conf: it is
                    // outside the marked set and outside the revert set, so neither header
                    // verb touches it.
                    Item { width: parent.width; height: Theme.spacingMd }
                    PRule { label: "Metadata" }
                    PRow {
                        label: "Title"
                        TextField {
                            id: titleField
                            width: 170; height: 24
                            text: editor.title
                            color: Theme.textPrimary; font.pixelSize: Theme.fontControl
                            background: Rectangle { color: Theme.inputWell; radius: Theme.radiusSm
                                border.width: 1
                                border.color: parent.activeFocus ? Theme.borderStrong : Theme.border }
                            onEditingFinished: editor.setTitle(text)
                        }
                    }
                    // Tags: the chip flow ends in a `+` pill that becomes an inline input in
                    // place; Enter commits, Esc cancels, the pill returns after either.
                    Item {
                        id: tagsRow
                        width: parent.width
                        implicitHeight: Math.max(34, tagFlow.implicitHeight + 8)
                        Label {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Tags"
                            color: Theme.textPrimary
                            font.pixelSize: view.fontRow
                        }
                        Flow {
                            id: tagFlow
                            anchors.right: parent.right
                            anchors.left: parent.left
                            anchors.leftMargin: 48
                            anchors.verticalCenter: parent.verticalCenter
                            layoutDirection: Qt.RightToLeft
                            spacing: Theme.spacingXs
                            property var tagList: (view.rev, editor.tags())
                            property bool adding: false

                            Rectangle {
                                id: addPill
                                height: 20
                                width: tagFlow.adding ? 96 : 22
                                radius: Theme.radiusXs
                                color: Theme.surfaceVariant
                                border.width: 1
                                border.color: addInput.activeFocus ? Theme.borderStrong : Theme.border
                                Label {
                                    visible: !tagFlow.adding
                                    anchors.centerIn: parent
                                    text: "+"
                                    color: Theme.textSecondary
                                    font.pixelSize: Theme.fontMeta
                                }
                                TextInput {
                                    id: addInput
                                    visible: tagFlow.adding
                                    anchors.fill: parent
                                    anchors.leftMargin: 6
                                    anchors.rightMargin: 6
                                    verticalAlignment: Text.AlignVCenter
                                    color: Theme.textPrimary
                                    font.pixelSize: Theme.fontMeta
                                    selectByMouse: true
                                    Keys.onEscapePressed: {
                                        addInput.text = "";
                                        tagFlow.adding = false;
                                        addInput.focus = false;
                                    }
                                    // losing focus for ANY reason (click-away, leaving the
                                    // editor) dismisses the box; Enter is the only commit
                                    onActiveFocusChanged: {
                                        if (!activeFocus && tagFlow.adding) {
                                            addInput.text = "";
                                            tagFlow.adding = false;
                                        }
                                    }
                                    onAccepted: {
                                        editor.addTag(addInput.text);
                                        addInput.text = "";
                                        tagFlow.adding = false;
                                        addInput.focus = false;
                                    }
                                }
                                HoverHandler { cursorShape: Qt.PointingHandCursor }
                                TapHandler {
                                    enabled: !tagFlow.adding
                                    onTapped: {
                                        tagFlow.adding = true;
                                        addInput.forceActiveFocus();
                                    }
                                }
                            }
                            Repeater {
                                model: tagFlow.tagList
                                delegate: Rectangle {
                                    id: tagChip
                                    required property string modelData
                                    height: 20
                                    width: tagLbl.implicitWidth + Theme.spacingSm * 2
                                    radius: Theme.radiusXs
                                    // the clicked chip holds a pressed wash while its confirm
                                    // popover is open, so the popover is anchored to a chip
                                    // the user can still see it belongs to
                                    color: confirmPop.visible ? Theme.activeWash : Theme.surfaceVariant
                                    Label { id: tagLbl; anchors.centerIn: parent; text: tagChip.modelData
                                            color: Theme.textMutedBody; font.pixelSize: Theme.fontMeta }
                                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: confirmPop.open() }

                                    // ConfirmPop: the ONE confirm step on this surface. Only
                                    // the red verb removes - Esc and clicking away cancel, and
                                    // Enter does not remove.
                                    Popup {
                                        id: confirmPop
                                        y: -height - 6
                                        margins: 8
                                        modal: false
                                        dim: false
                                        focus: true
                                        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                                        padding: 0
                                        background: Rectangle {
                                            color: Theme.surface
                                            radius: 8
                                            border.width: 1
                                            border.color: Theme.borderStrong
                                        }
                                        contentItem: Row {
                                            spacing: Theme.spacingMd
                                            leftPadding: 16
                                            rightPadding: 16
                                            topPadding: 14
                                            bottomPadding: 14
                                            Label {
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: "Remove tag?"
                                                color: Theme.textPrimary
                                                font.pixelSize: Theme.fontControl
                                            }
                                            Rectangle {
                                                anchors.verticalCenter: parent.verticalCenter
                                                height: 24
                                                width: rmLabel.implicitWidth + 18
                                                radius: 5
                                                color: rmHover.hovered ? Theme.dangerWash : "transparent"
                                                border.width: 1
                                                border.color: Theme.danger
                                                Label {
                                                    id: rmLabel
                                                    anchors.centerIn: parent
                                                    text: "Remove"
                                                    color: Theme.danger
                                                    font.pixelSize: Theme.fontMeta
                                                }
                                                HoverHandler { id: rmHover; cursorShape: Qt.PointingHandCursor }
                                                TapHandler {
                                                    onTapped: {
                                                        editor.removeTag(tagChip.modelData);
                                                        confirmPop.close();
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    PRow {
                        label: "Favorite"
                        ThemedSwitch {
                            id: favSwitch
                            checked: editor.favorite
                            onToggled: editor.toggleFavorite()
                        }
                    }
                    Item {
                        width: parent.width
                        implicitHeight: 28
                        Label {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Source"
                            color: Theme.textTertiary
                            font.pixelSize: view.fontRow
                        }
                        Label {
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            text: {
                                var parts = [];
                                if (editor.resolution) parts.push(editor.resolution);
                                // a numeric workshop id => the item came from the Steam workshop
                                if (/^[0-9]+$/.test(String(editor.wallpaperId))) parts.push("workshop");
                                parts.push(editor.type);
                                return parts.join(" · ");
                            }
                            color: Theme.textTertiary
                            font.pixelSize: Theme.fontMicro
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
                    }
                }
            }
        }
    }

    // background clicks never move QML focus, so the open tag box must catch outside
    // presses itself; the press passes through untouched to whatever was clicked
    MouseArea {
        anchors.fill: parent
        z: 1000
        enabled: tagFlow.adding
        onPressed: function(mouse) {
            var p = mapToItem(addPill, mouse.x, mouse.y);
            if (!(p.x >= 0 && p.y >= 0 && p.x <= addPill.width && p.y <= addPill.height)) {
                addInput.text = "";
                tagFlow.adding = false;
                addInput.focus = false;
            }
            mouse.accepted = false;
        }
    }
}
