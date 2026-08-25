import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import "."

Column {
    id: page

    property int rev: 0
    property int truthRev: 0
    Connections {
        target: settingsBridge
        function onChanged() { page.rev++ }
        function onTruthRefreshed() { page.truthRev++ }
    }

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

    property string pickKey: ""

    PRule { label: "Locations" }
    Item { width: 1; height: 23.75 - 12 }

    Repeater {
        model: [{"label": "Steam install",    "key": "STEAM_DIR"},
                {"label": "Workshop content", "key": "WORKSHOP_DIR"},
                {"label": "Library folder",   "key": "WALLPAPERS_DIR"},
                {"label": "Engine assets",    "key": "ASSETS_DIR"}]
        delegate: SettingsRow {
            id: locRow
            required property var modelData
            label: locRow.modelData.label

            Row {
                spacing: Theme.spacingSm
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    // HEAD-elided so the tail - the part that identifies the folder - stays
                    // readable. The ellipsis comes from Qt's own eliding and never from a
                    // string literal: tools/check_text.py fails on a U+2026 in source.
                    width: Math.min(implicitWidth, 380)
                    text: String(page.val(locRow.modelData.key) || "")
                    color: Theme.textTertiary
                    font.pixelSize: 11
                    font.family: Theme.monoFamily
                    elide: Text.ElideLeft
                    horizontalAlignment: Text.AlignRight
                }
                SettingsVerb {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Browse"
                    onClicked: { page.pickKey = locRow.modelData.key; folderPick.open(); }
                }
            }
        }
    }

    FolderDialog {
        id: folderPick
        // P21: a path picker commits on the dialog's ACCEPT - the only gesture a native
        // folder dialog offers. Cancel commits nothing.
        onAccepted: settingsBridge.commitPath(page.pickKey, selectedFolder)
    }

    Item { width: 1; height: 39 - 12 }
    PRule { label: "Detection" }
    Item { width: 1; height: 23.75 - 12 }

    SettingsRow {
        label: "Detect new items"
        SettingsCombo {
            id: detectCombo
            readonly property var vals: ["manual", "launch", "interval", "watch"]
            failed: page.isFailed("DETECT_MODE")
            model: ["Never", "On launch", "On a timer", "When the folder changes"]
            currentIndex: Math.max(0, vals.indexOf(String(page.val("DETECT_MODE"))))
            onActivated: function(i) {
                settingsBridge.commit("DETECT_MODE", detectCombo.vals[i]);
            }
        }
    }

    SettingsRow {
        label: "Check every"
        caption: "When detection runs on a timer"
        dim: String(page.val("DETECT_MODE")) !== "interval"
        SettingsField {
            width: 78
            suffix: "sec"
            enabled: String(page.val("DETECT_MODE")) === "interval"
            failed: page.isFailed("DETECT_INTERVAL_SEC")
            storeText: String(page.val("DETECT_INTERVAL_SEC") || "")
            onEntered: function(t) { settingsBridge.commit("DETECT_INTERVAL_SEC", t); }
        }
    }

    SettingsRow {
        label: "Rescan"
        SettingsVerb {
            id: rescanVerb
            text: "Rescan now"
            onClicked: { settingsBridge.rescanNow(); rescanLatch.restart(); }
        }
    }
    Timer { id: rescanLatch; interval: 5000 }

    Item { width: 1; height: 39 - 12 }
    PRule { label: "Import" }
    Item { width: 1; height: 23.75 - 12 }

    SettingsRow {
        label: "Require review"
        caption: "New items wait in Workshop until you approve them"
        ThemedSwitch {
            checked: page.val("REVIEW_REQUIRED") === true
            onToggled: settingsBridge.commit("REVIEW_REQUIRED", checked)
        }
    }

    SettingsRow {
        label: "Storage policy"
        caption: "Copy in survives unsubscribes; Reference saves disk"
        SegmentControl {
            id: policySeg
            sizeClass: "h22"
            readonly property var vals: ["copy", "reference"]
            model: ["Copy in", "Reference"]
            currentIndex: Math.max(0, vals.indexOf(String(page.val("STORAGE_POLICY"))))
            onActivated: function(i) {
                settingsBridge.commit("STORAGE_POLICY", policySeg.vals[i]);
            }
        }
    }

    Item { width: 1; height: 39 - 12 }
    PRule { label: "Storage" }
    Item { width: 1; height: 23.75 - 12 }

    SettingsRow {
        label: "Disk usage"
        Label {
            text: (page.rev, page.truthRev, settingsBridge.diskUsage())
            color: Theme.textTertiary
            font.pixelSize: 11
            horizontalAlignment: Text.AlignRight
        }
    }

    SettingsRow {
        label: "Tombstones"
        caption: "Trashed items that never reimport"
        Row {
            spacing: Theme.spacingSm
            Label {
                anchors.verticalCenter: parent.verticalCenter
                text: String((page.rev, page.truthRev, settingsBridge.tombstoneCount()))
                color: Theme.textTertiary
                font.pixelSize: 11
            }
            SettingsVerb {
                anchors.verticalCenter: parent.verticalCenter
                text: "Edit"
                onClicked: tombstones.openManager()
            }
        }
    }

    TombstoneManager {
        id: tombstones
        parent: Overlay.overlay
        // restore/purge inside the modal changes the count; refresh the row's read
        onCountChanged: page.truthRev++
    }

    Item { width: 1; height: 8 }
}
