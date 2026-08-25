import QtQuick
import QtQuick.Controls.Basic
import "."

Column {
    id: page

    property int rev: 0
    property int exRev: 0
    Connections {
        target: settingsBridge
        function onChanged() { page.rev++ }
        // list writes (exceptions / running-apps) announce on truthRefreshed; the child
        // rows' counts read through exRev, so an edit in the popup updates them live
        function onTruthRefreshed() { page.exRev++ }
    }

    property var dialSeed: []
    Component.onCompleted: page.dialSeed = settingsBridge.audioDials()
    function reseedDials() { page.dialSeed = settingsBridge.audioDials() }

    property var failedKeys: []
    function isFailed(key) { return key !== "" && page.failedKeys.indexOf(key) >= 0 }
    Connections {
        target: settingsBridge
        function onCommitFailed(keys, reason) { page.failedKeys = keys; failClear.restart(); }
    }
    Timer { id: failClear; interval: 2500; onTriggered: page.failedKeys = [] }

    function val(key) { return (page.rev, settingsBridge.value(key)) }

    width: parent ? parent.width : 0
    spacing: 0

    readonly property var speedKnotPos:   [0.0, 0.30, 0.65, 0.87, 1.0]
    readonly property var speedKnotValue: [0.1, 1.0,  2.0,  5.0,  10.0]
    readonly property real speedDetent: 1.0
    readonly property real speedDetentBand: 0.02
    readonly property real speedDetentPos: speedKnotPos[1]

    function speedForPos(p) {
        var pos = Math.max(0, Math.min(1, p));
        for (var i = 0; i < page.speedKnotPos.length - 1; i++) {
            var p0 = page.speedKnotPos[i], p1 = page.speedKnotPos[i + 1];
            if (pos > p1 && i < page.speedKnotPos.length - 2)
                continue;
            var v0 = page.speedKnotValue[i], v1 = page.speedKnotValue[i + 1];
            var t = (p1 === p0) ? 0 : (pos - p0) / (p1 - p0);
            return v0 * Math.pow(v1 / v0, t);
        }
        return page.speedKnotValue[page.speedKnotValue.length - 1];
    }
    function posForSpeed(v) {
        var s = Math.max(page.speedKnotValue[0],
                         Math.min(page.speedKnotValue[page.speedKnotValue.length - 1], Number(v)));
        for (var i = 0; i < page.speedKnotValue.length - 1; i++) {
            var v0 = page.speedKnotValue[i], v1 = page.speedKnotValue[i + 1];
            if (s > v1 && i < page.speedKnotValue.length - 2)
                continue;
            var p0 = page.speedKnotPos[i], p1 = page.speedKnotPos[i + 1];
            return p0 + (p1 - p0) * (Math.log(s / v0) / Math.log(v1 / v0));
        }
        return 1.0;
    }
    function speedDetented(v) {
        return Math.abs(v - page.speedDetent) <= page.speedDetentBand ? page.speedDetent : v;
    }
    function speedText(v) {
        var r = Number(Number(v).toPrecision(2));
        return ((r < 10 && r === Math.round(r)) ? r.toFixed(1) : String(r)) + "x";
    }

    PRule { label: "Rendering" }
    Item { width: 1; height: 23.75 - 12 }

    SettingsRow {
        label: "FPS"
        SettingsCombo {
            id: fpsCombo
            compact: true
            freeEntry: true                       // free integer entry 1-480, through the value
            failed: page.isFailed("ENGINE_FPS")
            model: ["Auto", "30", "60", "120", "144"]
            entryText: String(page.val("ENGINE_FPS") || "")
            currentIndex: {
                var v = String(page.val("ENGINE_FPS") || "");
                var i = ["Auto", "30", "60", "120", "144"].indexOf(v === "" ? "Auto" : v);
                return i;
            }
            displayText: {
                var v = String(page.val("ENGINE_FPS") || "");
                return v === "" ? "Auto" : v;
            }
            onActivated: function(i) {
                settingsBridge.commit("ENGINE_FPS", i === 0 ? "" : model[i]);
            }
            onEntered: function(t) { settingsBridge.commit("ENGINE_FPS", t); }
        }
    }

    SettingsRow {
        id: speedRow
        label: "Speed"
        readonly property real current: { var v = Number(page.val("ENGINE_TIMESCALE")); return isNaN(v) || v <= 0 ? 1.0 : v }

        Row {
            spacing: Theme.spacingSm
            SettingsSlider {
                id: speedSlider
                objectName: "settingsSpeedSlider"
                anchors.verticalCenter: parent.verticalCenter
                from: 0; to: 1
                tickAt: page.speedDetentPos
                storeValue: page.posForSpeed(speedRow.current)
                readonly property real speed: page.speedDetented(page.speedForPos(value))
                onCommit: function(p) {
                    // the detent settles through the STORE: commit writes the detented
                    // number, changed bumps rev, and the storeValue binding re-asserts -
                    // no imperative value write (that is what breaks slider bindings)
                    settingsBridge.commit("ENGINE_TIMESCALE",
                                          page.speedDetented(page.speedForPos(p)));
                }
            }
            SettingsCombo {
                id: speedValue
                objectName: "settingsSpeedChip"
                anchors.verticalCenter: parent.verticalCenter
                compact: true
                freeEntry: true
                failed: page.isFailed("ENGINE_TIMESCALE")
                model: []
                entryText: String(speedSlider.speed)
                displayText: page.speedText(speedSlider.speed)
                // free entry clamps to the ruled 0.1..10 band; anything outside is a
                // rejection with failure grammar, never a silent clamp
                onEntered: function(t) {
                    var v = Number(t);
                    if (isNaN(v) || v < 0.1 || v > 10)
                        settingsBridge.commit("ENGINE_TIMESCALE", t);
                    else
                        settingsBridge.commit("ENGINE_TIMESCALE", page.speedDetented(v));
                }
            }
        }
    }

    SettingsRow {
        label: "Scaling"
        SettingsCombo {
            id: scalingCombo
            readonly property var vals: ["default", "stretch", "fit", "fill"]
            failed: page.isFailed("ENGINE_SCALING")
            model: ["Default", "Stretch", "Fit", "Fill"]
            currentIndex: Math.max(0, vals.indexOf(String(page.val("ENGINE_SCALING"))))
            onActivated: function(i) {
                settingsBridge.commit("ENGINE_SCALING", scalingCombo.vals[i]);
            }
        }
    }

    SettingsRow {
        label: "Edge behavior"
        SettingsCombo {
            id: clampCombo
            readonly property var vals: ["", "clamp", "border", "repeat"]
            failed: page.isFailed("ENGINE_CLAMP")
            model: ["Engine default", "Hold the edge pixels", "Use the border color",
                    "Repeat the image"]
            currentIndex: Math.max(0, vals.indexOf(String(page.val("ENGINE_CLAMP") || "")))
            onActivated: function(i) {
                settingsBridge.commit("ENGINE_CLAMP", clampCombo.vals[i]);
            }
        }
    }

    Item { width: 1; height: 39 - 12 }
    PRule { label: "Audio" }
    Item { width: 1; height: 23.75 - 12 }

    SettingsRow {
        id: volumeRow
        label: "Volume"
        readonly property int current: Number(page.val("ENGINE_VOLUME") || 0)
        Row {
            spacing: Theme.spacingSm
            SettingsSlider {
                id: volumeSlider
                objectName: "settingsVolumeSlider"
                anchors.verticalCenter: parent.verticalCenter
                from: 0; to: 100; stepSize: 1
                storeValue: volumeRow.current
                onCommit: function(v) {
                    settingsBridge.commit("ENGINE_VOLUME", Math.round(v));
                }
            }
            SettingsCombo {
                objectName: "settingsVolumeChip"
                anchors.verticalCenter: parent.verticalCenter
                compact: true
                freeEntry: true
                failed: page.isFailed("ENGINE_VOLUME")
                model: []
                entryText: String(Math.round(volumeSlider.value))
                displayText: String(Math.round(volumeSlider.value))
                onEntered: function(t) { settingsBridge.commit("ENGINE_VOLUME", t); }
            }
        }
    }

    SettingsRow {
        label: "Auto-mute"
        caption: "When another app plays audio"
        ThemedSwitch {
            checked: page.val("AUTOMUTE_DEFAULT") === true
            onToggled: settingsBridge.commit("AUTOMUTE_DEFAULT", checked)
        }
    }

    SettingsRow {
        label: "Audio-reactive"
        caption: "Wallpapers respond to what is playing"
        ThemedSwitch {
            checked: page.val("AUDIO_REACTIVE_DEFAULT") === true
            onToggled: settingsBridge.commit("AUDIO_REACTIVE_DEFAULT", checked)
        }
    }

    Item { width: 1; height: 39 - 12 }
    PRule { label: "Audio response" }
    Item { width: 1; height: 23.75 - 12 }

    Repeater {
        // Seeded on load and after a DIAL commit only - never on the global rev, which
        // bumps on every settings write from any surface and would rebuild these rows
        // mid-drag (the delegate-rebuild disease, again)
        model: page.dialSeed
        delegate: SettingsRow {
            id: dialRow
            required property var modelData
            label: dialRow.modelData.label

            Row {
                spacing: Theme.spacingSm
                SettingsSlider {
                    id: dialSlider
                    anchors.verticalCenter: parent.verticalCenter
                    from: 0; to: 1
                    storeValue: dialRow.modelData.quality
                    onCommit: function(q) {
                        settingsBridge.setAudioDial(dialRow.modelData.key,
                                                    page.dialValue(dialRow.modelData, q));
                        page.reseedDials();
                    }
                }
                SettingsCombo {
                    anchors.verticalCenter: parent.verticalCenter
                    compact: true
                    freeEntry: true
                    failed: page.isFailed(dialRow.modelData.key)
                    model: []
                    // live off the slider, same law as Speed. The chip shows the 0..1
                    // QUALITY (what the label names); the store keeps the engine-native
                    // number, which is what free entry takes.
                    entryText: String(page.dialValue(dialRow.modelData, dialSlider.value))
                    displayText: Number(dialSlider.value).toFixed(2)
                    onEntered: function(t) {
                        settingsBridge.setAudioDial(dialRow.modelData.key, Number(t));
                    }
                }
            }
        }
    }

    Item {
        width: parent.width
        height: 22
        Label {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "These apply to every wallpaper."
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMicro
        }
    }

    // quality position -> engine-native value, the inverse of the bridge's own mapping
    function dialValue(spec, quality) {
        var q = Math.max(0, Math.min(1, quality));
        var t = spec.invert ? (1 - q) : q;
        if (spec.log) {
            var l0 = Math.log(spec.lo), l1 = Math.log(spec.hi);
            return Math.exp(l0 + (l1 - l0) * t);
        }
        return spec.lo + (spec.hi - spec.lo) * t;
    }

    Item { width: 1; height: 39 - 12 }
    PRule { label: "App rules" }
    Item { width: 1; height: 23.75 - 12 }

    component RuleChild: Item {
        id: child
        property string childLabel: ""
        property string childCaption: ""
        property int count: 0
        property string kind: ""
        property bool dimmed: false
        readonly property real fontRow: 12.5
        readonly property real fontCount: 11.5   // A1 sec 2; real-typed for the int coercion
        width: parent ? parent.width : 0
        height: 40
        opacity: dimmed ? 0.55 : 1.0
        Rectangle { x: 5; width: 1; height: parent.height; color: Theme.border }
        Item {
            anchors.fill: parent
            anchors.leftMargin: 5 + 12
            Column {
                anchors.left: parent.left
                anchors.right: countLabel.left
                anchors.rightMargin: Theme.spacingSm
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Label {
                    width: parent.width
                    text: child.childLabel
                    color: Theme.textPrimary
                    font.pixelSize: child.fontRow
                    elide: Text.ElideRight
                }
                Label {
                    width: parent.width
                    text: child.childCaption
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMicro
                    elide: Text.ElideRight
                }
            }
            Label {
                id: countLabel
                anchors.right: editBtn.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                text: child.count === 1 ? "1 app" : String(child.count) + " apps"
                color: Theme.textTertiary
                font.pixelSize: child.fontCount
            }
            Rectangle {
                id: editBtn
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                width: editLabel.implicitWidth + 20
                height: 24
                radius: 5
                color: "transparent"
                border.width: 1
                border.color: Theme.borderStrong
                Label {
                    id: editLabel
                    anchors.centerIn: parent
                    text: "Edit"
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontMeta
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: page.openListEditor(child.kind) }
            }
        }
    }

    AppListPopup { id: listEditor }
    function openListEditor(kind) {
        listEditor.kind = kind;
        listEditor.open();
    }

    SettingsRow {
        label: "While a game or app is fullscreen"
        SettingsCombo {
            id: fsCombo
            readonly property var vals: ["off", "pause", "stop"]
            model: ["Keep playing", "Pause", "Close LWE"]
            failed: page.isFailed("FULLSCREEN_BEHAVIOR")
            currentIndex: Math.max(0, vals.indexOf(String(page.val("FULLSCREEN_BEHAVIOR")
                                                          || "off")))
            onActivated: function(i) {
                settingsBridge.commit("FULLSCREEN_BEHAVIOR", fsCombo.vals[i]);
            }
        }
    }

    Item {
        width: parent.width
        readonly property string fsVal: String(page.val("FULLSCREEN_BEHAVIOR") || "off")
        readonly property string line: {
            if (fsVal === "pause")
                return "Rendering stops; memory stays for an instant resume.";
            if (fsVal === "stop")
                return "Frees everything, including video memory; takes a moment to come back.";
            return "";
        }
        height: line !== "" ? 22 : 0
        visible: line !== ""
        Label {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: parent.line
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMicro
        }
    }

    RuleChild {
        childLabel: "Exceptions"
        childCaption: "These apps never trigger the rule"
        count: (page.rev, page.exRev, settingsBridge.exceptionCount())
        kind: "exceptions"
        dimmed: String(page.val("FULLSCREEN_BEHAVIOR") || "off") === "off"
    }

    // A1 sec 2: 4px extra top margin separating rule 2 from the child block above
    Item { width: 1; height: 4 }

    SettingsRow {
        label: "While one of these apps is running"
        SettingsCombo {
            id: appCondCombo
            // Same vocabulary as the fullscreen row minus per-screen - no screen is
            // involved in a process condition (S-14). Strongest action wins when both
            // conditions fire.
            readonly property var vals: ["off", "pause", "stop"]
            failed: page.isFailed("APP_CONDITION_BEHAVIOR")
            model: ["Keep playing", "Pause", "Close LWE"]
            currentIndex: Math.max(0, vals.indexOf(String(page.val("APP_CONDITION_BEHAVIOR") || "off")))
            onActivated: function(i) {
                settingsBridge.commit("APP_CONDITION_BEHAVIOR", appCondCombo.vals[i]);
            }
        }
    }

    Item {
        // the same selected-state resource captions as the fullscreen rule [S-18] - the
        // pause/close semantics are identical; only the unavailable line is absent,
        // because a /proc poll needs no compositor
        width: parent.width
        readonly property string acVal: String(page.val("APP_CONDITION_BEHAVIOR") || "off")
        readonly property string line: {
            if (acVal === "pause")
                return "Rendering stops; memory stays for an instant resume.";
            if (acVal === "stop")
                return "Frees everything, including video memory; takes a moment to come back.";
            return "";
        }
        height: line !== "" ? 22 : 0
        visible: line !== ""
        Label {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: parent.line
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMicro
        }
    }

    RuleChild {
        childLabel: "Apps"
        childCaption: "The rule applies while any of these is open"
        count: (page.rev, page.exRev, settingsBridge.appEntryCount())
        kind: "apps"
        dimmed: String(page.val("APP_CONDITION_BEHAVIOR") || "off") === "off"
    }

    Item { width: 1; height: 39 - 12 }
    PRule { label: "Interaction" }
    Item { width: 1; height: 23.75 - 12 }

    SettingsRow {
        label: "Mouse input"
        ThemedSwitch {
            checked: page.val("MOUSE_DEFAULT") === true
            onToggled: settingsBridge.commit("MOUSE_DEFAULT", checked)
        }
    }
    SettingsRow {
        label: "Parallax"
        ThemedSwitch {
            checked: page.val("PARALLAX_DEFAULT") === true
            onToggled: settingsBridge.commit("PARALLAX_DEFAULT", checked)
        }
    }
    SettingsRow {
        label: "Particles"
        ThemedSwitch {
            checked: page.val("PARTICLES_DEFAULT") === true
            onToggled: settingsBridge.commit("PARTICLES_DEFAULT", checked)
        }
    }

    Item { width: 1; height: 39 - 12 }
    PRule { label: "Advanced" }
    Item { width: 1; height: 23.75 - 12 }

    SettingsRow {
        label: "Wayland layer"
        SettingsCombo {
            id: layerCombo
            readonly property var vals: ["background", "bottom", "top", "overlay"]
            failed: page.isFailed("ENGINE_LAYER")
            model: ["Background", "Bottom", "Top", "Overlay"]
            currentIndex: Math.max(0, vals.indexOf(String(page.val("ENGINE_LAYER"))))
            onActivated: function(i) {
                settingsBridge.commit("ENGINE_LAYER", layerCombo.vals[i]);
            }
        }
    }

    SettingsRow {
        label: "Video decode"
        SettingsCombo {
            id: hwdecCombo
            // Universal entries only (S-12.5): no vendor names anywhere in the UI. "auto"
            // lets mpv pick whatever decode hardware the system actually has, and the
            // entry name discloses the fallback honestly - bare "Hardware" would lie the
            // moment a codec falls back to software. Stored `nvdec` migrates to auto.
            readonly property var vals: ["no", "auto"]
            failed: page.isFailed("ENGINE_HWDEC")
            model: ["Software", "Hardware when available"]
            currentIndex: { var v = String(page.val("ENGINE_HWDEC")); return v === "nvdec" ? 1 : Math.max(0, vals.indexOf(v)) }
            onActivated: function(i) {
                settingsBridge.commit("ENGINE_HWDEC", hwdecCombo.vals[i]);
            }
        }
    }

    SettingsRow {
        label: "Texture detail"
        caption: "Automatic matches your display and frees the memory above it"
        SettingsCombo {
            id: texDetailCombo
            // mip residency (stint 5): entries name outcomes, not mechanisms. Automatic
            // keeps resolution along the author's own mip chain at the display's size,
            // streaming the full chain back the moment a scene truly magnifies into it.
            readonly property var vals: ["auto", "full"]
            failed: page.isFailed("TEXTURE_DETAIL")
            model: ["Automatic", "Full"]
            currentIndex: Math.max(0, vals.indexOf(String(page.val("TEXTURE_DETAIL") || "auto")))
            onActivated: function(i) {
                settingsBridge.commit("TEXTURE_DETAIL", texDetailCombo.vals[i]);
            }
        }
    }

    SettingsRow {
        label: "Texture compression"
        caption: "Trades a little banding for a lot of video memory"
        ThemedSwitch {
            checked: page.val("ENGINE_TEXCOMP") === true
            onToggled: settingsBridge.commit("ENGINE_TEXCOMP", checked)
        }
    }

    Item { width: 1; height: 8 }
}
