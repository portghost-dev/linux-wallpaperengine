import QtQuick
import QtQuick.Controls.Basic
import "."

Item {
    id: strip

    property bool opensUp: true   // deck opens menus upward; settings opens downward


    implicitWidth: outer.implicitWidth
    implicitHeight: 26

    property var activePl: ({slug: "", name: "", mode: "shuffle", interval: 900, unit: "min", count: 0})
    function refresh() { activePl = backend.activePlaylist() }
    Component.onCompleted: { refresh(); refreshSchedule() }
    Connections {
        target: backend
        function onPlaylistsChanged() { strip.refresh(); strip.refreshSchedule() }
        // getSetting is a plain slot with NO notify signal, so a naive binding to it freezes
        // (this exact trap froze the settings segment highlights in B6). Re-read on the
        // settings-changed pump instead.
        function onSettingsChanged() { strip.refreshSchedule() }
        function onStatusChanged() { strip.refreshSchedule() }
    }

    property bool schedEnabled: false
    property bool schedIsDay: true
    // the deck hands its status snapshot down so the cell re-reads day/night every tick
    property var engineStatus: ({})
    onEngineStatusChanged: refreshSchedule()
    function refreshSchedule() {
        var st = backend.scheduleState();
        schedEnabled = st.enabled === true;
        schedIsDay = st.is_day !== false;
    }
    signal scheduleRequested()

    function menuY(menu) { return strip.opensUp ? -menu.height - 4 : strip.height + 4 }
    function titleCase(s) { return s.length ? s.charAt(0).toUpperCase() + s.slice(1) : s }

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
                property string icon: ""         // "" | "moon" | "sun"
                property bool clock: false
                property bool inert: false         // the reserved cell: no hover, no tap
                property color iconColor: Theme.textTertiary
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
                        objectName: "cellMoon"
                        anchors.verticalCenter: parent.verticalCenter
                        visible: seg.icon === "moon"
                        size: 14
                        color: seg.iconColor
                    }
                    IconSun {
                        objectName: "cellSun"
                        anchors.verticalCenter: parent.verticalCenter
                        visible: seg.icon === "sun"
                        size: 14
                        color: seg.iconColor
                    }
                    IconClock {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: seg.clock
                        size: 14
                        color: clockMenu.visible ? Theme.textPrimary : Theme.textSecondary
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
                HoverHandler { id: segHover; enabled: !seg.inert }
                TapHandler { enabled: !seg.inert; onTapped: seg.tapped() }
            }

            StripSegment {
                objectName: "cellSchedule"
                // sun by day, moon by night, following the day range even when the
                // schedule is off; on, the icon takes amber by day and the accent by night
                icon: strip.schedIsDay ? "sun" : "moon"
                fixedWidth: 28
                roundLeft: true
                iconColor: !strip.schedEnabled ? Theme.textSecondary : (strip.schedIsDay ? Theme.warning : Theme.accent)
                tinted: strip.schedEnabled
                tintColor: strip.schedIsDay ? Qt.rgba(Theme.warning.r, Theme.warning.g, Theme.warning.b, 0.16)
                                            : Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.16)
                onTapped: strip.scheduleRequested()
            }
            Divider {}

            StripSegment {
                objectName: "cellName"
                filled: true
                chevron: true
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
            Divider {}
            // an empty cell the width of the clock cell, dividers and nothing inside, until the
            // display target lands or the cell is removed before any release
            StripSegment {
                objectName: "cellReserved"
                fixedWidth: 28
                inert: true
            }
            Divider {}
            StripSegment {
                objectName: "cellClock"
                fixedWidth: 28
                roundRight: true
                clock: true
                onTapped: {
                    if (clockMenu.visible) clockMenu.close();
                    else if (!clockMenu.justClosed) clockMenu.open();
                }
            }
        }
    }

    // Mode and Every live here and nowhere else
    ClockPopover {
        id: clockMenu
        objectName: "clockPopover"
        x: outer.width - width
        y: strip.menuY(clockMenu)
        activePl: strip.activePl
    }
    function openClock() { clockMenu.open() }

    Popup {
        id: nameMenu
        objectName: "nameMenu"
        x: outer.width - width
        y: strip.menuY(nameMenu)
        width: 210
        padding: Theme.spacingSm
        // toggle-race guard: the segment's tap fires AFTER press-outside already closed the
        // popup; this window (one tick over a double-click) lets that tap mean "close".
        property bool justClosed: false
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
            nameMenu.armedSlug = "";
        }
        onClosed: { justClosed = true; nameGuard.restart(); nameMenu.armedSlug = "" }
        property string entryMode: ""   // "" | "new" | "saveas" | "rename"
        // the one row whose trash was clicked: its confirm line is unfolded beneath it. Any
        // press that is not on its Yes disarms it, and the tap that press becomes is swallowed
        // (swallowTap) so cancelling never also selects, arms, or opens anything.
        property string armedSlug: ""
        property double armedAt: 0
        property Item armedYes: null
        property bool swallowTap: false
        // runs at every press inside the menu, before any tap: only a press on the armed
        // row's Yes keeps it armed; anything else disarms and marks the coming tap swallowed
        function settle(col, pos) {
            var yes = nameMenu.armedYes;
            var overYes = nameMenu.armedSlug !== "" && yes !== null && yes.visible
                          && yes.contains(col.mapToItem(yes, pos.x, pos.y));
            nameMenu.swallowTap = nameMenu.armedSlug !== "" && !overYes;
            if (!overYes) nameMenu.armedSlug = "";
        }
        function arm(slug) {
            if (nameMenu.armedSlug === slug) { nameMenu.armedSlug = ""; return }
            nameMenu.armedSlug = slug;
            nameMenu.armedAt = Date.now();
        }
        // Yes is inert for its first quarter second: a double-click can never reach it
        function yesReady() { return nameMenu.armedSlug !== "" && Date.now() - nameMenu.armedAt >= 250 }

        contentItem: Column {
            spacing: 2
            // holds the exclusive grab from the press to the release, even once the pointer has
            // left the menu, so a drag that starts here can never lift a tile beneath; it takes
            // nothing from the controls inside (TakeOverForbidden)
            TapHandler {
                gesturePolicy: TapHandler.ReleaseWithinBounds
                grabPermissions: PointerHandler.TakeOverForbidden
                onPressedChanged: if (pressed) nameMenu.settle(parent, point.pressPosition)
            }

            Repeater {
                id: plRepeater
                model: []
                delegate: Item {
                    id: plRow
                    required property var modelData
                    readonly property bool isActive: plRow.modelData.slug === strip.activePl.slug
                    readonly property bool armed: nameMenu.armedSlug === plRow.modelData.slug
                    onArmedChanged: if (armed) nameMenu.armedYes = yesBtn
                    objectName: "plRow"
                    width: nameMenu.width - Theme.spacingSm * 2
                    height: 26 + (armed ? 26 : 0)
                    Rectangle {
                        width: parent.width
                        height: 26
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
                        y: 13 - implicitHeight / 2
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.spacingSm + (plRow.isActive ? checkSlot.width + Theme.spacingXs : 0)
                        text: plRow.modelData.name
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                        elide: Text.ElideRight
                        width: parent.width - countLbl.width - trash.width - Theme.spacingLg - Theme.spacingSm - (plRow.isActive ? checkSlot.width + Theme.spacingXs : 0)
                    }
                    Label {
                        id: countLbl
                        y: 13 - implicitHeight / 2
                        anchors.right: trash.left
                        anchors.rightMargin: Theme.spacingSm
                        text: plRow.modelData.count
                        color: Theme.textTertiary
                        font.pixelSize: Theme.fontMeta
                    }
                    // the row's own delete: arms the confirm line beneath; a second click disarms
                    Item {
                        id: trash
                        objectName: "rowTrash"
                        width: 18
                        height: 26
                        anchors.right: parent.right
                        anchors.rightMargin: Theme.spacingXs
                        IconTrash {
                            anchors.centerIn: parent
                            size: 12
                            color: plRow.armed || trashHover.hovered ? Theme.danger : Theme.textTertiary
                        }
                        HoverHandler { id: trashHover }
                        TapHandler { onTapped: if (!nameMenu.swallowTap) nameMenu.arm(plRow.modelData.slug) }
                    }
                    HoverHandler { id: plRowHover }
                    // the row body: everything left of the trash on the name line. The trash and
                    // the confirm line sit outside it, so their taps never fall through to here
                    Item {
                        objectName: "rowBody"
                        width: parent.width - trash.width - Theme.spacingXs
                        height: 26
                        TapHandler {
                            onTapped: {
                                if (nameMenu.swallowTap) return;
                                backend.setActivePlaylist(plRow.modelData.slug);
                                nameMenu.close();
                            }
                        }
                    }
                    // the confirm line, unfolded beneath the armed row and indented under its name
                    Item {
                        objectName: "confirmLine"
                        visible: plRow.armed
                        y: 26
                        width: parent.width
                        height: 26
                        Label {
                            id: confirmLbl
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: parent.left
                            anchors.leftMargin: Theme.spacingLg
                            text: "Delete?"
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontMeta
                        }
                        Rectangle {
                            id: yesBtn
                            objectName: "confirmYes"
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.right: noBtn.left
                            anchors.rightMargin: Theme.spacingSm
                            width: yesLbl.implicitWidth + Theme.spacingMd * 2
                            height: 20
                            radius: Theme.radiusXs
                            color: Theme.dangerWash
                            Label {
                                id: yesLbl
                                anchors.centerIn: parent
                                text: "Yes"
                                color: Theme.danger
                                font.pixelSize: Theme.fontMeta
                            }
                            TapHandler {
                                onTapped: {
                                    if (!nameMenu.yesReady()) return;
                                    var slug = plRow.modelData.slug;
                                    nameMenu.armedSlug = "";
                                    backend.deletePlaylist(slug);
                                    nameMenu.close();
                                }
                            }
                        }
                        Item {
                            id: noBtn
                            objectName: "confirmNo"
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.right: parent.right
                            anchors.rightMargin: Theme.spacingXs
                            width: noLbl.implicitWidth + Theme.spacingMd * 2
                            height: 20
                            Label {
                                id: noLbl
                                anchors.centerIn: parent
                                text: "No"
                                color: Theme.textSecondary
                                font.pixelSize: Theme.fontMeta
                            }
                            TapHandler { onTapped: nameMenu.armedSlug = "" }
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
                TapHandler { onTapped: if (!nameMenu.swallowTap) ma.tapped() }
            }

            Row {
                visible: nameMenu.entryMode !== ""
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

        }
    }
}
