import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import "."

Column {
    id: page

    // S8: no control is a load-time snapshot. Every value is read through a function of
    // `rev`, so a popup or editor commit refreshes this page without a rebuild.
    property int rev: 0
    property int truthRev: 0
    Connections {
        target: settingsBridge
        function onChanged() { page.rev++ }
        function onTruthRefreshed() { page.truthRev++ }
    }

    // Failure grammar mirror (S5): the shell owns the banner, every page owns the outline
    // on its own failed control. Both ride the same signal and the same 2500ms.
    property var failedKeys: []
    function isFailed(key) { return key !== "" && page.failedKeys.indexOf(key) >= 0 }
    Connections {
        target: settingsBridge
        function onCommitFailed(keys, reason) { page.failedKeys = keys; failClear.restart(); }
    }
    Timer { id: failClear; interval: 2500; onTriggered: page.failedKeys = [] }

    function val(key) { return (page.rev, settingsBridge.value(key)) }
    function truth() { return (page.rev, page.truthRev, settingsBridge.systemTruth()) }

    width: parent ? parent.width : 0
    spacing: 0

    PSection { label: "App"; first: true }

    SettingsRow {
        label: "Start on login"
        ThemedSwitch {
            checked: (page.rev, settingsBridge.autostart())
            onToggled: settingsBridge.setAutostart(checked)
        }
    }

    SettingsRow {
        label: "Close to tray"
        ThemedSwitch {
            checked: page.val("CLOSE_TO_TRAY") === true
            onToggled: settingsBridge.commit("CLOSE_TO_TRAY", checked)
        }
    }

    SettingsRow {
        label: "Interface scale"
        caption: "Applies after relaunch"
        Column {
            id: scaleCtl
            objectName: "interfaceScale"
            spacing: 4
            topPadding: 6
            bottomPadding: 6
            readonly property int trackW: 260
            readonly property int knobW: 10
            readonly property var detents: [75, 100, 150]
            readonly property real glyphFont: 8.5
            readonly property color tickInk: Theme.isLight
                ? Qt.rgba(Theme.textPrimary.r, Theme.textPrimary.g, Theme.textPrimary.b, 0.30)
                : Qt.rgba(1, 1, 1, 0.30)
            // linear position map: the knob centre sits at (v - 75) / 75 of the track
            function xFor(v) { return (v - 75) / 75 * scaleCtl.trackW }

            // one monitor glyph per detent, centred on its tick, lit when it is the value
            Item {
                x: scaleCtl.knobW / 2
                width: scaleCtl.trackW
                height: 25.5
                Repeater {
                    model: [{"v": 75, "t": "1080p"}, {"v": 100, "t": "1440p"}, {"v": 150, "t": "2160p"}]
                    delegate: Item {
                        id: glyph
                        required property var modelData
                        objectName: "scaleGlyph"
                        x: scaleCtl.xFor(modelData.v) - 17
                        width: 34
                        height: parent.height
                        readonly property bool current: scaleSlider.shown === modelData.v
                        readonly property color ink: current ? Theme.textPrimary : Theme.textSecondary
                        Rectangle {
                            width: 34; height: 22; radius: 3
                            color: "transparent"
                            border.width: 1.5
                            border.color: glyph.ink
                            Label {
                                anchors.centerIn: parent
                                text: glyph.modelData.t
                                color: glyph.ink
                                font.pixelSize: scaleCtl.glyphFont
                            }
                        }
                        Rectangle { x: 12; y: 24; width: 10; height: 1.5; color: glyph.ink }
                    }
                }
            }

            Row {
                spacing: 10
                Slider {
                    id: scaleSlider
                    objectName: "interfaceScaleSlider"
                    // the knob's travel is the track: Qt maps a press over width - knob, so
                    // the slider is the track plus one knob and the track is drawn inset
                    width: scaleCtl.trackW + scaleCtl.knobW
                    height: 16
                    padding: 0
                    from: 75
                    to: 150
                    stepSize: 1
                    readonly property int stored: (page.rev, Math.round(Number(page.val("INTERFACE_SCALE"))) || 100)
                    // the chip and the glyphs read the knob while it is held, the store otherwise
                    readonly property int shown: pressed ? Math.round(value) : stored
                    Binding {
                        target: scaleSlider
                        property: "value"
                        value: scaleSlider.stored
                        when: !scaleSlider.pressed
                        restoreMode: Binding.RestoreBindingOrValue
                    }
                    onPressedChanged: {
                        if (pressed) return;
                        var v = settingsBridge.settleScale(value);
                        value = v;
                        settingsBridge.commit("INTERFACE_SCALE", v);
                    }
                    background: Rectangle {
                        x: scaleCtl.knobW / 2
                        y: scaleSlider.height / 2 - height / 2
                        width: scaleCtl.trackW
                        height: 3
                        radius: 1.5
                        color: Theme.border
                        Rectangle {
                            width: scaleSlider.visualPosition * parent.width
                            height: parent.height
                            radius: 1.5
                            color: Theme.accent
                        }
                        Repeater {
                            model: scaleCtl.detents
                            delegate: Rectangle {
                                required property int modelData
                                objectName: "scaleTick"
                                x: scaleCtl.xFor(modelData) - 0.75
                                y: -3
                                width: 1.5
                                height: 9
                                color: scaleCtl.tickInk
                            }
                        }
                    }
                    handle: Rectangle {
                        x: scaleSlider.visualPosition * scaleCtl.trackW
                        y: scaleSlider.height / 2 - height / 2
                        width: scaleCtl.knobW
                        height: scaleCtl.knobW
                        radius: scaleCtl.knobW / 2
                        color: Theme.textPrimary
                    }
                }
                Rectangle {
                    objectName: "interfaceScaleChip"
                    anchors.verticalCenter: parent.verticalCenter
                    height: 22
                    width: Math.max(46, chipLabel.implicitWidth + 16)
                    radius: 5
                    color: Theme.surface
                    border.width: 1
                    border.color: page.isFailed("INTERFACE_SCALE") ? Theme.danger : Theme.border
                    Label {
                        id: chipLabel
                        anchors.centerIn: parent
                        text: scaleSlider.shown + "%"
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontMeta
                    }
                }
            }
        }
    }

    PSection { label: "System" }

    SettingsRow {
        label: "Engine mode"
        Label {
            text: {
                var t = page.truth();
                return t.socketLive ? "Daemon · live control on"
                                    : "Daemon · socket not answering";
            }
            color: Theme.textTertiary
            font.pixelSize: 11
            horizontalAlignment: Text.AlignRight
        }
    }

    SettingsRow {
        label: "Memory limit"
        caption: "Set by the service file"
        Label {
            // Parsed from the LIVE unit file, not from the generator's template: a
            // hand-edited unit is what systemd actually enforces (sec 4.1, Q14). G9 - a
            // missing or unparsable file says so rather than fabricating a number (G9).
            // The unknown string is [PROPOSED].
            text: {
                var t = page.truth();
                if (!t.memoryHigh || !t.memoryMax) return "Unknown";
                return String(t.memoryHigh).replace("G", " GB") + " high · "
                     + String(t.memoryMax).replace("G", " GB") + " max";
            }
            color: Theme.textTertiary
            font.pixelSize: 11
            horizontalAlignment: Text.AlignRight
        }
    }

    SettingsRow {
        label: "Logs"
        // a FILE MANAGER, never a terminal
        SettingsVerb { text: "Open logs"; onClicked: settingsBridge.openLogs() }
    }

    SettingsRow {
        id: configRow
        label: "Configuration"
        // the receipt of the last import this session; empty until then
        caption: (page.rev, settingsBridge.receiptLine)

        Row {
            spacing: 10
            SettingsVerb { text: "Export"; onClicked: exportDialog.open() }
            SettingsVerb { text: "Import"; onClicked: importDialog.open() }
            SettingsVerb {
                id: resetVerb
                text: "Reset"
                danger: true
                onClicked: resetConfirm.open(resetVerb)
            }
        }
    }

    ConfirmPop {
        id: resetConfirm
        prompt: "Reset all settings?"
        verb: "Reset"
        danger: true
        onConfirmed: settingsBridge.resetConfig()
    }

    FileDialog {
        id: exportDialog
        fileMode: FileDialog.SaveFile
        nameFilters: [settingsBridge.backupFilter()]
        defaultSuffix: "lwebackup"
        onAccepted: settingsBridge.exportBackup(selectedFile)
        onVisibleChanged: if (visible) selectedFile = currentFolder + "/" + settingsBridge.backupDefaultName()
    }
    FileDialog {
        id: importDialog
        fileMode: FileDialog.OpenFile
        nameFilters: [settingsBridge.backupFilter()]
        onAccepted: settingsBridge.importBackup(selectedFile)
    }

    Item { width: 1; height: 8 }
}
