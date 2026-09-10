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
