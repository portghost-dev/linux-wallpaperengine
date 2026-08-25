import QtQuick
import QtQuick.Controls.Basic
import "."

Item {
    id: strip

    property bool opensUp: true   // deck opens menus upward; settings opens downward

    property bool foldable: false
    readonly property bool folded: foldable && Theme.compact

    implicitWidth: outer.implicitWidth
    implicitHeight: 26

    property var activePl: ({slug: "", name: "", mode: "shuffle", interval: 900, unit: "min", count: 0})
    function refresh() { activePl = backend.activePlaylist() }
    Component.onCompleted: { refresh(); refreshSchedule() }
    Connections {
        target: backend
        function onPlaylistsChanged() { strip.refresh() }
        // getSetting is a plain slot with NO notify signal, so a naive binding to it freezes
        // (this exact trap froze the settings segment highlights in B6). Re-read on the
        // settings-changed pump instead.
        function onSettingsChanged() { strip.refreshSchedule() }
    }

    property bool schedEnabled: false
    function refreshSchedule() { schedEnabled = backend.getSetting("SCHEDULE_ENABLED") === true }
    signal scheduleRequested()

    function menuY(menu) { return strip.opensUp ? -menu.height - 4 : strip.height + 4 }
    function titleCase(s) { return s.length ? s.charAt(0).toUpperCase() + s.slice(1) : s }
    // the interval as the two entry fields DISPLAY it (seconds verbatim, else minutes) -
    // the comparand that keeps a commit from re-firing when nothing actually changed
    function shownInterval() {
        var iv = strip.activePl.interval || 900;
        return strip.activePl.unit === "s" ? iv : Math.round(iv / 60);
    }

    Rectangle {
        id: outer
        implicitWidth: row.implicitWidth + 2   // + the 1px outer border on each side
        implicitHeight: 26
        width: implicitWidth
        height: implicitHeight
        color: Theme.surface
        radius: Theme.radiusSm
        border.width: 1
        border.color: Theme.borderStrong
        clip: true

        Row {
            id: row
            anchors.fill: parent
            anchors.margins: 1

            component Divider: Rectangle {
                width: 1
                height: parent.height
                color: Theme.border
            }

            component StripSegment: Item {
                id: seg
                property alias label: segLabel.text
                property bool filled: false
                property bool dimmed: false
                property bool textPrimary: false
                property bool chevron: false
                property bool moon: false
                property color moonColor: Theme.textTertiary
                property bool tinted: false        // status tint, distinct from `filled`
                property color tintColor: "transparent"
                property int fixedWidth: 0         // 0 = size to content
                property bool roundLeft: false     // leftmost cell: round its left corners to
                property bool roundRight: false    // the outer's INNER radius so a filled cell
                                                   // never squares off the rounded border
                                                   // (the outer's clip is rectangular)
                signal tapped()
                height: row.height
                width: seg.fixedWidth > 0 ? seg.fixedWidth
                                          : content.implicitWidth + Theme.spacingSm * 2
                opacity: dimmed ? 0.4 : 1
                Rectangle {
                    anchors.fill: parent
                    // gutter law (standard segmented-control construction): a fill must never
                    // touch the outer border. Inset the fill 1px on every side that meets the
                    // border - top/bottom always (the strip is one row, so every cell abuts
                    // the top and bottom border), left/right only on the end cells (interior
                    // sides meet a divider, not the border). A hairline of base surface then
                    // separates fill from border on all sides and the border reads an
                    // identical weight everywhere, so no per-side contrast hack is needed.
                    anchors.topMargin: 1
                    anchors.bottomMargin: 1
                    anchors.leftMargin: seg.roundLeft ? 1 : 0
                    anchors.rightMargin: seg.roundRight ? 1 : 0
                    color: seg.filled ? Theme.surfaceVariant
                         : seg.tinted ? seg.tintColor
                         : (segHover.hovered ? Theme.hoverWash : "transparent")
                    // fill radius = outer radius - 1 (the outer's inner curve)
                    topLeftRadius: seg.roundLeft ? Theme.radiusSm - 1 : 0
                    bottomLeftRadius: seg.roundLeft ? Theme.radiusSm - 1 : 0
                    topRightRadius: seg.roundRight ? Theme.radiusSm - 1 : 0
                    bottomRightRadius: seg.roundRight ? Theme.radiusSm - 1 : 0
                }
                Row {
                    id: content
                    anchors.centerIn: parent
                    spacing: Theme.spacingXs
                    IconMoon {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: seg.moon
                        size: 14
                        color: seg.moonColor
                    }
                    Label {
                        id: segLabel
                        anchors.verticalCenter: parent.verticalCenter
                        color: (seg.filled || seg.textPrimary) ? Theme.textPrimary : Theme.textTertiary
                        font.pixelSize: Theme.fontControl
                    }
                    IconChevron {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: seg.chevron
                        direction: "down"
                        color: Theme.textSecondary
                    }
                }
                HoverHandler { id: segHover }
                TapHandler { onTapped: seg.tapped() }
            }

            StripSegment {
                moon: true
                fixedWidth: 28
                roundLeft: true
                moonColor: strip.schedEnabled ? Theme.accent : Theme.textTertiary
                tinted: strip.schedEnabled
                tintColor: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.15)
                onTapped: strip.scheduleRequested()
            }
            Divider {}

            StripSegment {
                filled: true
                chevron: true
                roundRight: strip.folded
                label: {
                    var n = strip.activePl.name || "no playlist";
                    return n.length > 18 ? n.substring(0, 18) : n;
                }
                // clicking the open segment must CLOSE the menu. The popup's press-outside
                // policy already closed it on this press, so by tap time visible is false and
                // a naive toggle reopens it - the justClosed window breaks that race.
                onTapped: {
                    if (nameMenu.visible) nameMenu.close();
                    else if (!nameMenu.justClosed) nameMenu.open();
                }
            }
            Divider { visible: !strip.folded }
            StripSegment {
                visible: !strip.folded
                textPrimary: true
                chevron: true
                label: strip.titleCase(strip.activePl.mode || "shuffle")
                onTapped: {
                    if (modeMenu.visible) modeMenu.close();
                    else if (!modeMenu.justClosed) modeMenu.open();
                }
            }
            Divider { visible: !strip.folded }
            Item {
                visible: !strip.folded
                height: row.height
                width: intervalField.width + Theme.spacingSm
                opacity: strip.activePl.mode === "static" ? 0.4 : 1
                TextField {
                    id: intervalField
                    objectName: "intervalField"
                    anchors.centerIn: parent
                    width: 42
                    enabled: strip.activePl.mode !== "static"
                    text: {
                        var iv = strip.activePl.interval || 900;
                        return strip.activePl.unit === "s" ? String(iv) : String(Math.round(iv / 60));
                    }
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontControl
                    horizontalAlignment: Text.AlignHCenter
                    validator: IntValidator { bottom: 1; top: 9999 }
                    background: Item {}
                    // Enter commits AND releases the caret: editingFinished fires for
                    // both Enter and focus loss, so the commit is guarded against
                    // writing the same value twice on the way out.
                    onEditingFinished: {
                        var v = parseInt(text);
                        if (isNaN(v) || v === strip.shownInterval())
                            return;
                        backend.setPlaylistInterval(v, strip.activePl.unit || "min");
                    }
                    onAccepted: focus = false
                }
            }
            StripSegment {
                visible: !strip.folded
                label: "min"
                filled: strip.activePl.unit !== "s"
                dimmed: strip.activePl.mode === "static"
                onTapped: {
                    if (strip.activePl.mode === "static") return;
                    if (strip.activePl.unit !== "s") return;
                    var iv = strip.activePl.interval || 900;
                    backend.setPlaylistInterval(Math.max(1, Math.round(iv / 60)), "min");
                }
            }
            StripSegment {
                visible: !strip.folded
                label: "s"
                roundRight: true
                filled: strip.activePl.unit === "s"
                dimmed: strip.activePl.mode === "static"
                onTapped: {
                    if (strip.activePl.mode === "static") return;
                    if (strip.activePl.unit === "s") return;
                    backend.setPlaylistInterval(strip.activePl.interval || 900, "s");
                }
            }
        }
    }

    Popup {
        id: nameMenu
        x: outer.width - width
        y: strip.menuY(nameMenu)
        width: 210
        padding: Theme.spacingSm
        // toggle-race guard: the segment's tap fires AFTER press-outside already closed the
        // popup; this window (one tick over a double-click) lets that tap mean "close".
        property bool justClosed: false
        onClosed: { justClosed = true; nameGuard.restart() }
        Timer { id: nameGuard; interval: 150; onTriggered: nameMenu.justClosed = false }
        background: Rectangle {
            color: Theme.surfaceVariant
            radius: Theme.radiusMd
            border.width: 1
            border.color: Theme.borderStrong
        }
        onOpened: {
            plRepeater.model = backend.playlistList();
            nameMenu.entryMode = "";
            nameMenu.foldedModesOpen = false;
        }
        property string entryMode: ""   // "" | "new" | "saveas" | "rename" | "confirm-delete"
        // folded (compact) only: the Mode row expands its four options inline
        property bool foldedModesOpen: false

        contentItem: Column {
            spacing: 2

            Repeater {
                id: plRepeater
                model: []
                delegate: Item {
                    id: plRow
                    required property var modelData
                    readonly property bool isActive: plRow.modelData.slug === strip.activePl.slug
                    width: nameMenu.width - Theme.spacingSm * 2
                    height: 26
                    Rectangle {
                        anchors.fill: parent
                        radius: Theme.radiusXs
                        color: plRow.isActive ? Theme.hoverWash
                             : plRowHover.hovered ? Theme.hoverWash : "transparent"
                    }
                    Item {
                        id: checkSlot
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.spacingSm
                        width: 10
                        height: 8
                        visible: plRow.isActive
                        Rectangle { x: 0; y: 4; width: 5; height: 2; radius: 1; rotation: 45; color: Theme.accent }
                        Rectangle { x: 3; y: 3; width: 8; height: 2; radius: 1; rotation: -50; color: Theme.accent }
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.spacingSm + (plRow.isActive ? checkSlot.width + Theme.spacingXs : 0)
                        text: plRow.modelData.name
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                        elide: Text.ElideRight
                        width: parent.width - countLbl.width - Theme.spacingLg - (plRow.isActive ? checkSlot.width + Theme.spacingXs : 0)
                    }
                    Label {
                        id: countLbl
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.right: parent.right
                        anchors.rightMargin: Theme.spacingSm
                        text: plRow.modelData.count
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMeta
                    }
                    HoverHandler { id: plRowHover }
                    TapHandler {
                        onTapped: {
                            backend.setActivePlaylist(plRow.modelData.slug);
                            nameMenu.close();
                        }
                    }
                }
            }

            Rectangle { width: nameMenu.width - Theme.spacingSm * 2; height: 1; color: Theme.border }

            component MenuAction: Item {
                id: ma
                property string label: ""
                property bool danger: false
                signal tapped()
                width: nameMenu.width - Theme.spacingSm * 2
                height: 24
                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radiusXs
                    color: maHover.hovered ? (ma.danger ? Theme.dangerWash : Theme.hoverWash) : "transparent"
                }
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.spacingSm
                    text: ma.label
                    color: ma.danger ? Theme.danger : Theme.textSecondary
                    font.pixelSize: Theme.fontControl
                }
                HoverHandler { id: maHover }
                TapHandler { onTapped: ma.tapped() }
            }

            Row {
                visible: nameMenu.entryMode !== "" && nameMenu.entryMode !== "confirm-delete"
                spacing: Theme.spacingXs
                TextField {
                    id: entryField
                    width: nameMenu.width - Theme.spacingSm * 2 - 34
                    height: 24
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontControl
                    placeholderText: nameMenu.entryMode === "rename" ? "New name" : "Playlist name"
                    background: Rectangle {
                        color: Theme.inputWell
                        radius: Theme.radiusXs
                        border.width: 1
                        border.color: entryField.activeFocus ? Theme.borderStrong : Theme.border
                    }
                    onAccepted: entryOk.tapped()
                }
                MenuAction {
                    id: entryOk
                    width: 30
                    label: "ok"
                    onTapped: {
                        var n = entryField.text.trim();
                        if (n === "") return;
                        if (nameMenu.entryMode === "new")
                            backend.createPlaylist(n);
                        else if (nameMenu.entryMode === "saveas")
                            backend.saveAsPlaylist(n);
                        else if (nameMenu.entryMode === "rename")
                            backend.renameActivePlaylist(n);
                        entryField.text = "";
                        nameMenu.close();
                    }
                }
            }

            MenuAction {
                visible: nameMenu.entryMode === ""
                label: "New playlist"
                onTapped: { nameMenu.entryMode = "new"; entryField.forceActiveFocus() }
            }
            MenuAction {
                visible: nameMenu.entryMode === ""
                label: "Save as"
                onTapped: { nameMenu.entryMode = "saveas"; entryField.forceActiveFocus() }
            }
            MenuAction {
                visible: nameMenu.entryMode === ""
                label: "Rename"
                onTapped: { nameMenu.entryMode = "rename"; entryField.forceActiveFocus() }
            }
            MenuAction {
                visible: nameMenu.entryMode === ""
                danger: true
                label: "Delete"
                onTapped: nameMenu.entryMode = "confirm-delete"
            }
            MenuAction {
                visible: nameMenu.entryMode === "confirm-delete"
                danger: true
                label: "Really delete " + (strip.activePl.name || "") + "?"
                onTapped: { backend.deleteActivePlaylist(); nameMenu.close() }
            }

            Rectangle {
                visible: strip.folded
                width: nameMenu.width - Theme.spacingSm * 2
                height: 1
                color: Theme.border
            }
            Item {
                visible: strip.folded
                width: nameMenu.width - Theme.spacingSm * 2
                height: 24
                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radiusXs
                    color: fModeHover.hovered ? Theme.hoverWash : "transparent"
                }
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.spacingSm
                    text: "Mode"
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontControl
                }
                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right
                    anchors.rightMargin: Theme.spacingSm
                    spacing: Theme.spacingXs
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        text: strip.titleCase(strip.activePl.mode || "shuffle")
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                    }
                    IconChevron {
                        anchors.verticalCenter: parent.verticalCenter
                        direction: nameMenu.foldedModesOpen ? "up" : "down"
                        color: Theme.textSecondary
                    }
                }
                HoverHandler { id: fModeHover }
                TapHandler { onTapped: nameMenu.foldedModesOpen = !nameMenu.foldedModesOpen }
            }
            Repeater {
                model: (strip.folded && nameMenu.foldedModesOpen)
                       ? ["shuffle", "random", "sequential", "static"] : []
                delegate: Item {
                    id: fModeRow
                    required property string modelData
                    width: nameMenu.width - Theme.spacingSm * 2
                    height: 24
                    Rectangle {
                        anchors.fill: parent
                        radius: Theme.radiusXs
                        color: fModeRow.modelData === strip.activePl.mode ? Theme.selectionWash
                             : fModeRowHover.hovered ? Theme.hoverWash : "transparent"
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.spacingLg
                        text: strip.titleCase(fModeRow.modelData)
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                    }
                    HoverHandler { id: fModeRowHover }
                    TapHandler {
                        onTapped: {
                            backend.setPlaylistMode(fModeRow.modelData);
                            nameMenu.foldedModesOpen = false;
                        }
                    }
                }
            }
            Item {
                visible: strip.folded
                width: nameMenu.width - Theme.spacingSm * 2
                height: 26
                opacity: strip.activePl.mode === "static" ? 0.4 : 1
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.spacingSm
                    text: "Every"
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontControl
                }
                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right
                    anchors.rightMargin: Theme.spacingSm
                    spacing: Theme.spacingXs
                    TextField {
                        id: fIntervalField
                        objectName: "fIntervalField"
                        anchors.verticalCenter: parent.verticalCenter
                        width: 42
                        height: 22
                        enabled: strip.activePl.mode !== "static"
                        text: {
                            var iv = strip.activePl.interval || 900;
                            return strip.activePl.unit === "s" ? String(iv)
                                                               : String(Math.round(iv / 60));
                        }
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                        horizontalAlignment: Text.AlignHCenter
                        validator: IntValidator { bottom: 1; top: 9999 }
                        background: Rectangle {
                            color: Theme.inputWell
                            radius: Theme.radiusXs
                            border.width: 1
                            border.color: fIntervalField.activeFocus ? Theme.borderStrong
                                                                     : Theme.border
                        }
                        onEditingFinished: {
                            var v = parseInt(text);
                            if (isNaN(v) || v === strip.shownInterval())
                                return;
                            backend.setPlaylistInterval(v, strip.activePl.unit || "min");
                        }
                        onAccepted: focus = false
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "min"
                        color: strip.activePl.unit !== "s" ? Theme.textPrimary : Theme.textTertiary
                        font.pixelSize: Theme.fontControl
                        TapHandler {
                            onTapped: {
                                if (strip.activePl.mode === "static") return;
                                if (strip.activePl.unit !== "s") return;
                                var iv = strip.activePl.interval || 900;
                                backend.setPlaylistInterval(Math.max(1, Math.round(iv / 60)), "min");
                            }
                        }
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "s"
                        color: strip.activePl.unit === "s" ? Theme.textPrimary : Theme.textTertiary
                        font.pixelSize: Theme.fontControl
                        TapHandler {
                            onTapped: {
                                if (strip.activePl.mode === "static") return;
                                if (strip.activePl.unit === "s") return;
                                backend.setPlaylistInterval(strip.activePl.interval || 900, "s");
                            }
                        }
                    }
                }
            }
        }
    }

    Popup {
        id: modeMenu
        x: outer.width - width
        y: strip.menuY(modeMenu)
        width: 130
        padding: Theme.spacingSm
        property bool justClosed: false   // same toggle-race guard as the name menu
        onClosed: { justClosed = true; modeGuard.restart() }
        Timer { id: modeGuard; interval: 150; onTriggered: modeMenu.justClosed = false }
        background: Rectangle {
            color: Theme.surfaceVariant
            radius: Theme.radiusMd
            border.width: 1
            border.color: Theme.borderStrong
        }
        contentItem: Column {
            spacing: 2
            Repeater {
                model: ["shuffle", "random", "sequential", "static"]
                delegate: Item {
                    id: modeRow
                    required property string modelData
                    width: modeMenu.width - Theme.spacingSm * 2
                    height: 24
                    Rectangle {
                        anchors.fill: parent
                        radius: Theme.radiusXs
                        color: modeRow.modelData === strip.activePl.mode ? Theme.selectionWash
                             : modeRowHover.hovered ? Theme.hoverWash : "transparent"
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.spacingSm
                        text: strip.titleCase(modeRow.modelData)
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                    }
                    HoverHandler { id: modeRowHover }
                    TapHandler {
                        onTapped: {
                            backend.setPlaylistMode(modeRow.modelData);
                            modeMenu.close();
                        }
                    }
                }
            }
        }
    }
}
