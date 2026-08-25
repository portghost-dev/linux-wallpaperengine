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

    PRule { label: "App" }
    Item { width: 1; height: 23.75 - 12 }

    SettingsRow {
        label: "Start on login"
        ThemedSwitch {
            checked: (page.rev, settingsBridge.autostart())
            onToggled: settingsBridge.setAutostart(checked)
        }
    }

    SettingsRow {
        label: "Close to tray"
        caption: "The app keeps running in the tray"
        ThemedSwitch {
            checked: page.val("CLOSE_TO_TRAY") === true
            onToggled: settingsBridge.commit("CLOSE_TO_TRAY", checked)
        }
    }

    Column {
        id: scheduleSection
        objectName: "scheduleSection"
        width: parent.width
        spacing: 0
        visible: settingsBridge.scheduleUi()
        height: visible ? implicitHeight : 0

        readonly property bool on: page.val("SCHEDULE_ENABLED") === true
        readonly property var packed: String(page.val("SCHEDULE") || "").split(";")

        function entryTime(i) {
            var e = String(scheduleSection.packed[i] || "");
            return e.indexOf("=") >= 0 ? e.split("=")[0] : "";
        }
        function entrySlug(i) {
            var e = String(scheduleSection.packed[i] || "");
            return e.indexOf("=") >= 0 ? e.split("=")[1] : "";
        }
        // "HH:MM=slug;HH:MM=slug" - the packing format is unchanged (constants.py SCHEDULE)
        function repack(i, time, slug) {
            var a = [scheduleSection.entryTime(0) + "=" + scheduleSection.entrySlug(0),
                     scheduleSection.entryTime(1) + "=" + scheduleSection.entrySlug(1)];
            a[i] = time + "=" + slug;
            settingsBridge.commit("SCHEDULE", a.join(";"));
        }

        Item { width: 1; height: 39 - 12 }
        PRule { label: "Schedule" }
        Item { width: 1; height: 23.75 - 12 }

        SettingsRow {
            label: "Switch playlists by time of day"
            ThemedSwitch {
                checked: scheduleSection.on
                onToggled: settingsBridge.commit("SCHEDULE_ENABLED", checked)
            }
        }

        Repeater {
            model: [{"label": "Daytime playlist", "idx": 0},
                    {"label": "Night playlist", "idx": 1}]
            delegate: SettingsRow {
                id: schedRow
                required property var modelData
                label: schedRow.modelData.label
                dim: !scheduleSection.on

                Row {
                    spacing: Theme.spacingSm
                    SettingsCombo {
                        id: plCombo
                        enabled: scheduleSection.on
                        model: (page.rev, settingsBridge.playlistSlugs())
                        textRole: "name"
                        valueRole: "slug"
                        failed: page.isFailed("SCHEDULE")
                        currentIndex: {
                            var slug = scheduleSection.entrySlug(schedRow.modelData.idx);
                            var list = settingsBridge.playlistSlugs();
                            for (var i = 0; i < list.length; i++)
                                if (list[i].slug === slug) return i;
                            return -1;
                        }
                        onActivated: function(i) {
                            var list = settingsBridge.playlistSlugs();
                            scheduleSection.repack(
                                schedRow.modelData.idx,
                                scheduleSection.entryTime(schedRow.modelData.idx),
                                i >= 0 && i < list.length ? list[i].slug : "");
                        }
                    }
                    SettingsField {
                        width: 60
                        enabled: scheduleSection.on
                        failed: page.isFailed("SCHEDULE")
                        storeText: scheduleSection.entryTime(schedRow.modelData.idx)
                        // commits on Enter or blur; anything that is not HH:MM 24-hour is
                        // rejected with failure grammar, never coerced into a nearby time
                        onEntered: function(t) {
                            scheduleSection.repack(
                                schedRow.modelData.idx, t,
                                scheduleSection.entrySlug(schedRow.modelData.idx));
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
                text: "Changes at the next rotation, never mid-wallpaper."
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMicro
            }
        }
    }

    Item { width: 1; height: 39 - 12 }
    PRule { label: "System" }
    Item { width: 1; height: 23.75 - 12 }

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
        caption: "Reset keeps the engine mode"

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

    FolderDialog {
        id: exportDialog
        onAccepted: settingsBridge.exportConfig(selectedFolder)
    }
    FolderDialog {
        id: importDialog
        onAccepted: settingsBridge.importConfig(selectedFolder)
    }

    Item { width: 1; height: 8 }
}
