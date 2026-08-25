import QtQuick
import QtQuick.Controls.Basic
import "."

RecordsFace {
    id: modal
    objectName: "tombstoneManager"

    // The modal does not reach into the import pipeline itself - it reports, and the surface
    // that owns the grid rescans. Dropping the gate only un-suppresses the NEXT scan, so without
    // a rescan the tile area does not repopulate until the folder watcher happens to fire
    // (measured ~5s). The old header ghost paired bypassImport with rescanNow; deleting the ghost
    // took the rescan with it.
    signal importPerformed()
    // NOTE: no explicit countChanged signal. RecordsFace already declares `property int count`,
    // whose auto-generated change signal is exactly that name - declaring it again is a duplicate
    // override and the type fails to load. Since count binds to rows.length, Settings > Library's
    // onCountChanged fires by itself whenever the list changes, which is what it wanted.

    property var rows: []
    property string expandedWid: ""
    property var timelineRows: []
    function toggleExpand(wid) {
        if (expandedWid === wid) { expandedWid = ""; timelineRows = []; }
        else { expandedWid = wid; timelineRows = workshop.recordTimeline(wid); }
    }
    property string confirmWid: ""
    property bool confirmAll: false

    title: "Tombstones"
    count: rows.length
    subtitle: "Trashed items that never reimport on their own. Import brings one back to Workshop; purge erases the record."
    filterable: rows.length > 10

    readonly property var shown: {
        if (filterText === "")
            return rows;
        var f = filterText.toLowerCase(), out = [];
        for (var i = 0; i < rows.length; i++)
            if (String(rows[i].title).toLowerCase().indexOf(f) !== -1)
                out.push(rows[i]);
        return out;
    }
    readonly property int importableCount: {
        var n = 0;
        for (var i = 0; i < rows.length; i++) if (rows[i].importable) n++;
        return n;
    }

    function reload() {
        rows = workshop.recordList();
        confirmWid = "";
        confirmAll = false;
        expandedWid = "";
        timelineRows = [];
    }
    function openManager() { resetFilter(); reload(); open() }
    function openModal() { openManager() }

    readonly property int rowH: 42
    readonly property int maxRows: 6

    // The viewport SNAPS to whole rows instead of sitting at a flat 252. At a fixed height a
    // short list left dead space between the last row's rule and the footer's, which read as a
    // thick or doubled border; snapping puts the footer rule exactly where a between-row rule
    // would fall. NOTE: this only holds at rest and at full scroll - mid-scroll a partial row
    // still meets the footer. Making that exact needs scroll snapping, which is a behavior
    // change and Design's to spec.
    ListView {
        id: list
        objectName: "tombstoneList"
        width: parent.width
        height: Math.max(modal.rowH,
                         Math.min(modal.maxRows, modal.shown.length) * modal.rowH)
        clip: true
        model: modal.shown
        boundsBehavior: Flickable.StopAtBounds
        // 24c: a 3px ALWAYS-visible bar. Controls-Basic's stock handle renders near-invisible
        // against this surface, so both track and handle are styled from tokens explicitly.
        ScrollBar.vertical: ScrollBar {
            id: vbar
            policy: ScrollBar.AlwaysOn
            // padding 0 is LOAD-BEARING. ScrollBar carries 2px of its own padding per side, so
            // `width: 3` left 3 - 2 - 2 = -1 for the handle, clamped to zero: the bar existed at
            // full height and correct position, with an infinitely thin handle. That is why only
            // the track ever showed. Introspection caught it (vhandle w=0.0); pixels alone did not.
            padding: 0
            width: 3
            anchors.right: parent.right
            anchors.rightMargin: 3   // Design's render: 3px handle, 3px clear of the frame
            // NO track: "3px scrollbar" is the handle itself. Styling the background put a gray
            // frame down the gutter, which is all that showed - because the handle below had no
            // implicit size and collapsed to nothing. The stock Basic contentItem carries
            // implicitWidth/implicitHeight (6, or 2 when non-interactive); an override that omits
            // them renders zero-sized.
            background: Item {}
            contentItem: Rectangle {
                implicitWidth: 3
                implicitHeight: 24
                radius: 1.5
                color: vbar.pressed ? Theme.textSecondary : Theme.hairlineStrong
            }
        }

        Label {
            anchors.centerIn: parent
            visible: modal.rows.length === 0
            text: "No tombstones yet"
            color: Theme.textTertiary
            font.pixelSize: Theme.fontBody13
        }

        delegate: Item {
            id: row
            required property var modelData
            required property int index
            readonly property bool isConfirm: modal.confirmWid === row.modelData.wid
            readonly property bool canImport: row.modelData.importable === true
            readonly property bool isManual: row.modelData.manual === true
            readonly property bool isOpen: modal.expandedWid === row.modelData.wid
            width: list.width
            height: modal.rowH + (isOpen ? modal.timelineRows.length * 22 + 8 : 0)

            // the whole collapsed row is the expand target (buttons stop the tap themselves)
            TapHandler { onTapped: modal.toggleExpand(row.modelData.wid) }

            IconChevron {
                id: chev
                anchors.left: parent.left
                anchors.leftMargin: Theme.spacingSm
                y: (modal.rowH - height) / 2
                size: 12
                direction: row.isOpen ? "down" : "right"
                color: Theme.textTertiary
            }

            Column {
                anchors.left: chev.right
                anchors.leftMargin: Theme.spacingSm
                anchors.right: metaRow.left
                anchors.rightMargin: Theme.spacingMd
                y: (modal.rowH - height) / 2
                spacing: 2
                Label {
                    width: parent.width
                    text: row.modelData.title
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontBody13
                    elide: Text.ElideRight
                }
                Label {
                    width: parent.width
                    visible: !row.canImport
                    text: "unsubscribed · files gone"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMeta
                    elide: Text.ElideRight
                }
            }

            Row {
                id: metaRow
                anchors.right: importBtn.left
                anchors.rightMargin: Theme.spacingMd
                y: (modal.rowH - height) / 2
                spacing: Theme.spacingMd
                Chip {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: row.modelData.outcome !== ""
                    kind: "fact"
                    tone: row.modelData.crashed ? Theme.danger : Theme.textTertiary
                    text: row.modelData.outcome
                }
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    text: row.modelData.date
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMeta
                }
            }

            Button {
                id: unsubBtn
                anchors.right: purgeBtn.left
                anchors.rightMargin: Theme.spacingXs
                y: (modal.rowH - height) / 2
                height: 24
                visible: !row.isManual
                width: visible ? implicitWidth : 0
                enabled: row.canImport
                text: "Unsubscribe"
                onClicked: workshop.openItemPage(row.modelData.wid)
                contentItem: Label {
                    text: unsubBtn.text
                    color: unsubBtn.enabled ? Theme.textSecondary : Theme.textTertiary
                    font.pixelSize: Theme.fontMeta
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    implicitWidth: 88
                    radius: Theme.radiusSm
                    color: unsubBtn.hovered && unsubBtn.enabled ? Theme.hoverWash : "transparent"
                    border.width: 1
                    border.color: unsubBtn.enabled ? Theme.hairlineStrong : Theme.hairlineFaint
                }
            }

            Button {
                id: importBtn
                anchors.right: unsubBtn.left
                anchors.rightMargin: Theme.spacingXs
                y: (modal.rowH - height) / 2
                height: 24
                enabled: row.canImport
                text: "Import"
                onClicked: {
                    if (workshop.bypassImportOne(row.modelData.wid))
                        modal.importPerformed();
                    modal.reload();
                }
                contentItem: Label {
                    text: importBtn.text
                    color: importBtn.enabled ? Theme.onAccent : Theme.textTertiary
                    font.pixelSize: Theme.fontMeta
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    implicitWidth: 54
                    radius: Theme.radiusSm
                    color: !importBtn.enabled ? Theme.surfaceVariant
                         : importBtn.hovered ? Qt.lighter(Theme.accent, 1.15)
                         : Theme.accent
                    border.width: 0
                }
            }

            Button {
                id: purgeBtn
                anchors.right: parent.right
                anchors.rightMargin: Theme.spacingSm + 6
                y: (modal.rowH - height) / 2
                height: 24
                text: row.isConfirm ? "Confirm" : "Purge"
                onClicked: {
                    if (row.isConfirm) {
                        workshop.purgeRecord(row.modelData.wid);
                        modal.reload();
                    } else {
                        modal.confirmWid = row.modelData.wid;
                    }
                }
                contentItem: Label {
                    text: purgeBtn.text
                    color: Theme.danger
                    font.pixelSize: Theme.fontMeta
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    implicitWidth: 60
                    radius: Theme.radiusSm
                    color: (purgeBtn.hovered || row.isConfirm) ? Theme.dangerWash : "transparent"
                    border.width: 0
                }
            }

            Column {
                y: modal.rowH
                anchors.left: parent.left
                anchors.leftMargin: Theme.spacingSm + 12 + Theme.spacingSm
                anchors.right: parent.right
                anchors.rightMargin: Theme.spacingSm + 6
                visible: row.isOpen
                spacing: 0
                Repeater {
                    model: row.isOpen ? modal.timelineRows : []
                    delegate: Item {
                        required property var modelData
                        width: parent.width
                        height: 22
                        Rectangle {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            width: 5; height: 5; radius: 2.5
                            color: Theme.borderStrong
                        }
                        Label {
                            anchors.left: parent.left
                            anchors.leftMargin: Theme.spacingMd
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData.line
                            color: Theme.textTertiary
                            font.pixelSize: Theme.fontMeta
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            // the LAST row draws no rule: the footer's own rule closes the list, and drawing
            // both stacked them into the thick border
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.hairlineFaint     // row divider, .06 - HALF the structural rule.
                                               // Collapsing both onto one token is what drew these
                                               // at double weight.
                visible: row.index < list.count - 1
            }
        }
    }

    footer: Item {
        width: parent.width
        height: 58

        Button {
            id: importAll
            anchors.left: parent.left
            anchors.leftMargin: Theme.spacingLg
            anchors.verticalCenter: parent.verticalCenter
            height: 28
            enabled: modal.importableCount > 0
            opacity: enabled ? 1 : 0.35
            text: "Import all (" + modal.importableCount + ")"
            onClicked: { workshop.bypassImport(); modal.importPerformed(); modal.reload() }
            contentItem: Label {
                text: importAll.text
                color: Theme.onAccent
                font.pixelSize: Theme.fontControl
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                implicitWidth: 130
                radius: Theme.radiusSm
                color: importAll.hovered && importAll.enabled
                       ? Qt.lighter(Theme.accent, 1.15) : Theme.accent
                border.width: 0
            }
        }

        Button {
            id: purgeAll
            anchors.left: importAll.right
            anchors.leftMargin: Theme.spacingSm
            anchors.verticalCenter: parent.verticalCenter
            height: 28
            visible: modal.rows.length > 0
            text: modal.confirmAll ? "Confirm" : "Purge all"
            onClicked: {
                if (modal.confirmAll) {
                    workshop.purgeAllRecords();
                    modal.reload();
                } else {
                    modal.confirmAll = true;
                }
            }
            contentItem: Label {
                text: purgeAll.text
                color: Theme.danger
                font.pixelSize: Theme.fontControl
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                implicitWidth: 66
                radius: Theme.radiusSm
                color: (purgeAll.hovered || modal.confirmAll) ? Theme.dangerWash : "transparent"
                border.width: 1
                border.color: modal.confirmAll
                              ? Theme.danger
                              : Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.47)
            }
        }

        Label {
            anchors.right: parent.right
            anchors.rightMargin: Theme.spacingLg
            anchors.verticalCenter: parent.verticalCenter
            text: "Imported items return to Workshop for benching"
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMeta
        }
    }
}
