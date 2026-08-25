import QtQuick
import QtQuick.Controls.Basic
import "."

Item {
    id: panel

    property int rev: 0
    property bool showHeading: true
    property bool compactMode: false
    property string heading: "Object Exclusion"
    property string caption: ""
    // the overlay scrollbar rides this column's own right edge, so the CONTENT reserves the
    // clearance rather than the viewport being pulled in (L-14; see EditorView.contentInset)
    property real barGutter: 16

    property var objs: (rev, editor.objectList())
    property var typeCounts: (rev, editor.objectTypeCounts())
    property bool treeAvailable: (rev, editor.hasParenting())
    // "authored" -> group by the scene author's names; "type" -> fall back to engine type.
    property string groupingMode: (rev, editor.groupingMode())
    property string typeFilter: "all"
    property string search: ""
    property bool treeMode: false

    // per-group collapse state (default expanded), keyed by group key (authored name or type).
    property var collapsed: ({})
    property int rebuild: 0                   // bump to re-run the flat-model builder

    Connections {
        target: editor
        function onWallpaperChanged() {
            panel.typeFilter = "all";
            panel.search = "";
            panel.treeMode = false;
            panel.collapsed = ({});
            objSearch.text = "";
            panel.rebuild++;
        }
    }

    function isCollapsed(k) { return panel.collapsed[k] === true; }
    function toggleGroup(k) {
        var c = panel.collapsed;
        c[k] = !(c[k] === true);
        panel.collapsed = c;                 // reassign so bindings re-evaluate
        panel.rebuild++;
    }

    // The key an object groups under in the current mode: its authored name, or its engine
    // type when the scene exposes no names. Empty-name objects in an otherwise-named scene
    // fall back to their type so they still land in a labeled group.
    function groupKeyOf(o) {
        if (panel.groupingMode === "authored")
            return String(o.name || "") !== "" ? String(o.name) : String(o.type || "generic");
        return String(o.type || "generic");
    }
    function groupSubLabel(gtype, count) {
        return count > 1 ? (count + " " + gtype) : gtype;
    }

    function accepts(o) {
        if (typeFilter !== "all" && o.type !== typeFilter) return false;
        if (search !== "") {
            var s = search.toLowerCase();
            if ((String(o.name) + " " + String(o.objid)).toLowerCase().indexOf(s) < 0) return false;
        }
        return true;
    }

    // the live tri-state of the current filtered view, re-read on every SKIP change
    readonly property var bulkState: (rev, panel.rebuild, editor.filteredSkipState(panel.typeFilter))

    component TriToggle: Item {
        id: tri
        property string state3: "on"          // "on" | "off" | "partial"
        signal flipped(bool on)
        implicitWidth: 30
        implicitHeight: 17
        Rectangle {
            anchors.fill: parent
            radius: 9
            color: tri.state3 === "on" ? Theme.accent : Theme.toggleOffTrack
            border.width: tri.state3 === "on" ? 0 : 1
            border.color: Theme.toggleOffBorder
            Rectangle {
                width: 13; height: 13; radius: 6.5
                anchors.verticalCenter: parent.verticalCenter
                x: tri.state3 === "on" ? parent.width - width - 2
                 : tri.state3 === "off" ? 2
                 : (parent.width - width) / 2
                color: tri.state3 === "off" ? Theme.toggleOffKnob : Theme.textPrimary
                Behavior on x { NumberAnimation { duration: 90 } }
            }
        }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: tri.flipped(tri.state3 !== "on") }
    }

    property Component headerControls: Component {
        Row {
            spacing: Theme.spacingSm

            Rectangle {
                id: typeDrop
                anchors.verticalCenter: parent.verticalCenter
                height: 26
                    width: Math.max(96, typeLabel.implicitWidth + 32)
                    radius: 6
                    color: Theme.surface
                    border.width: 1
                    border.color: Theme.border
                    readonly property var entries: {
                        var out = [{ label: "All types", value: "all" }];
                        for (var i = 0; i < panel.typeCounts.length; i++)
                            out.push({ label: panel.typeCounts[i].count + " " + panel.typeCounts[i].type,
                                       value: panel.typeCounts[i].type });
                        return out;
                    }
                    Label {
                        id: typeLabel
                        anchors.left: parent.left
                        anchors.leftMargin: 10
                        anchors.right: typeCaret.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: {
                            if (panel.typeFilter === "all") return "All types";
                            for (var i = 0; i < panel.typeCounts.length; i++)
                                if (panel.typeCounts[i].type === panel.typeFilter)
                                    return panel.typeCounts[i].count + " " + panel.typeFilter;
                            return panel.typeFilter;
                        }
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                        elide: Text.ElideRight
                    }
                    Item {
                        id: typeCaret
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: 26
                        IconChevron {
                            anchors.right: parent.right
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            direction: "down"
                            size: 10
                            color: Theme.textSecondary
                        }
                    }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            if (typeMenu.visible) typeMenu.close();
                            else if (!typeMenu.justClosed) typeMenu.open();
                        }
                    }
                Menu {
                    id: typeMenu
                    y: typeDrop.height + 2
                    property bool justClosed: false
                    onClosed: { justClosed = true; typeGuard.restart() }
                    Timer { id: typeGuard; interval: 150; onTriggered: typeMenu.justClosed = false }
                    background: Rectangle {
                        implicitWidth: 150
                        color: Theme.surfaceVariant
                        radius: Theme.radiusSm
                        border.width: 1
                        border.color: Theme.borderStrong
                    }
                    Repeater {
                        model: typeDrop.entries
                        delegate: ThemedMenuItem {
                            required property var modelData
                            text: modelData.label
                            onTriggered: { panel.typeFilter = String(modelData.value); panel.rebuild++; }
                        }
                    }
                }
            }

            TriToggle {
                anchors.verticalCenter: parent.verticalCenter
                state3: String(panel.bulkState.state || "on")
                onFlipped: function(on) {
                    editor.setFilteredEnabled(panel.typeFilter, on);
                    panel.rebuild++;
                }
            }
        }
    }

    Column {
        anchors.fill: parent
        spacing: Theme.spacingSm

        Item {
            width: parent.width - panel.barGutter
            visible: panel.showHeading
            height: visible ? Math.max(headingCol.implicitHeight, hdrControls.height) : 0

            Column {
                id: headingCol
                anchors.left: parent.left
                anchors.right: hdrControls.left
                anchors.rightMargin: Theme.spacingMd
                anchors.top: parent.top
                spacing: 2
                Label {
                    width: parent.width
                    text: panel.heading + " · " + panel.objs.length
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontControl; font.weight: Theme.weightMedium
                    elide: Text.ElideRight
                }
                Label {
                    width: parent.width
                    visible: panel.caption !== ""
                    text: panel.caption
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMicro
                    elide: Text.ElideRight
                }
            }
            Loader {
                id: hdrControls
                anchors.right: parent.right
                anchors.top: parent.top
                sourceComponent: panel.headerControls
            }
        }

        Item {
            width: parent.width - panel.barGutter
            height: 26

            SegmentControl {
                id: viewSeg
                visible: !panel.compactMode
                width: visible ? implicitWidth : 0
                sizeClass: "h22"
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                model: ["Groups", "Tree"]
                currentIndex: panel.treeMode ? 1 : 0
                onActivated: function(i) { panel.treeMode = (i === 1); panel.rebuild++; }
            }
            TextField {
                id: objSearch
                anchors.left: viewSeg.visible ? viewSeg.right : parent.left
                anchors.leftMargin: viewSeg.visible ? Theme.spacingSm : 0
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                height: 24
                placeholderText: "name or id"
                color: Theme.textPrimary; font.pixelSize: Theme.fontMeta
                font.family: Theme.monoFamily
                background: Rectangle { color: Theme.inputWell; radius: Theme.radiusXs
                    border.width: 1; border.color: parent.activeFocus ? Theme.borderStrong : Theme.border }
                onTextEdited: { panel.search = text; panel.rebuild++; }
            }
        }

        // virtualized object list; grouped by authored name (or type fallback) unless in tree mode
        ListView {
            id: objList
            width: parent.width
            height: Math.max(0, parent.height - y)
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: {
                panel.rebuild;                 // dependency: collapse toggles rebuild the model
                var rows = [];
                var filtered = panel.objs.filter(panel.accepts);
                if (panel.treeMode && panel.treeAvailable) {
                    // parents first, then their children indented (single level shown)
                    var byParent = {};
                    for (var i = 0; i < filtered.length; i++) {
                        var p = filtered[i].parent || "";
                        (byParent[p] = byParent[p] || []).push(filtered[i]);
                    }
                    var roots = byParent[""] || filtered;
                    for (var r = 0; r < roots.length; r++) {
                        rows.push({kind: "obj", depth: 0, o: roots[r]});
                        var kids = byParent[roots[r].objid] || [];
                        for (var k = 0; k < kids.length; k++)
                            rows.push({kind: "obj", depth: 1, o: kids[k]});
                    }
                } else {
                    // group by authored name or engine type (fallback); order = first-seen.
                    var byGroup = {};
                    var order = [];
                    var meta = {};                 // group key -> {type, count, byName}
                    for (var j = 0; j < filtered.length; j++) {
                        var key = panel.groupKeyOf(filtered[j]);
                        // byName is true only when the key came from a real authored name (so
                        // the cascade routes to setAuthoredGroupEnabled). An empty-name object
                        // in an otherwise-named scene keys under its TYPE - that group routes
                        // to the unnamed-type toggle instead, so the switch stays live and a
                        // plain type cascade cannot flip named-group members with it.
                        var keyed = panel.groupingMode === "authored"
                                    && String(filtered[j].name || "") !== "";
                        if (!byGroup[key]) {
                            byGroup[key] = []; order.push(key);
                            meta[key] = {type: String(filtered[j].type || "generic"), count: 0,
                                         mixed: false, byName: keyed};
                        }
                        byGroup[key].push(filtered[j]);
                        if (meta[key].type !== String(filtered[j].type || "generic"))
                            meta[key].mixed = true;
                    }
                    for (var g = 0; g < order.length; g++) {
                        var gk = order[g];
                        var items = byGroup[gk];
                        // group enabled = not every child skipped (mirrors editor.authoredGroups)
                        var anyOn = false;
                        for (var e = 0; e < items.length; e++) if (!items[e].skipped) { anyOn = true; break; }
                        var gtype = meta[gk].mixed ? "mixed" : meta[gk].type;
                        rows.push({kind: "group", key: gk, label: gk, type: gtype,
                                   count: items.length, enabled: anyOn, byName: meta[gk].byName,
                                   collapsed: panel.isCollapsed(gk)});
                        if (!panel.isCollapsed(gk)) {
                            for (var m = 0; m < items.length; m++)
                                rows.push({kind: "obj", depth: 1, o: items[m]});
                        }
                    }
                }
                return rows;
            }

            ScrollBar.vertical: ScrollBar {
                id: objBar
                policy: ScrollBar.AsNeeded
                rightPadding: 3
                background: Item {}
                contentItem: Rectangle {
                    implicitWidth: 4
                    radius: 2
                    color: Qt.rgba(1, 1, 1, 0.25)
                    opacity: objBar.active ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 150 } }
                }
            }

            delegate: Item {
                id: rowItem
                required property var modelData
                width: objList.width - panel.barGutter
                height: rowItem.modelData.kind === "group" ? 29 : 26

                Item {
                    visible: rowItem.modelData.kind === "group"
                    anchors.fill: parent
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1
                                color: Theme.border }
                    Row {
                        anchors.left: parent.left
                        anchors.right: grpSwitch.left
                        anchors.rightMargin: Theme.spacingSm
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 9
                        Item {
                            width: 10; height: parent.height
                            anchors.verticalCenter: parent.verticalCenter
                            Item {
                                anchors.centerIn: parent
                                width: 5; height: 5
                                rotation: rowItem.modelData.collapsed ? -45 : 45
                                Rectangle { anchors.right: parent.right; width: 5; height: 1.5; color: Theme.textTertiary
                                            transformOrigin: Item.Center }
                                Rectangle { anchors.bottom: parent.bottom; width: 1.5; height: 5; color: Theme.textTertiary }
                            }
                        }
                        Label { anchors.verticalCenter: parent.verticalCenter
                                text: rowItem.modelData.label || ""; color: Theme.textPrimary
                                font.pixelSize: Theme.fontControl
                                elide: Text.ElideRight
                                width: Math.max(0, Math.min(implicitWidth, parent.width - 10 - subLbl.implicitWidth - 18)) }
                        Label { id: subLbl; anchors.verticalCenter: parent.verticalCenter
                                text: panel.groupSubLabel(rowItem.modelData.type || "",
                                                          rowItem.modelData.count || 0)
                                color: Theme.textTertiary; font.pixelSize: Theme.fontMicro }
                    }
                    ThemedSwitch {
                        id: grpSwitch
                        anchors.right: parent.right
                        // ThemedSwitch centers its VISIBLE pill inside a 64px hit target,
                        // leaving (width-pillWidth)/2 of phantom margin - pull the container
                        // right so the PILL rides the content line (measured 35px off, 29a
                        // law is flush; the oversized hit area overhangs the bar reserve,
                        // which is empty air)
                        anchors.rightMargin: -((width - pillWidth) / 2)
                        anchors.verticalCenter: parent.verticalCenter
                        pillWidth: 26; pillHeight: 15
                        checked: rowItem.modelData.enabled === true
                        onToggled: {
                            if (rowItem.modelData.byName === true)
                                editor.setAuthoredGroupEnabled(rowItem.modelData.key, checked);
                            else
                                editor.setUnnamedGroupEnabled(rowItem.modelData.key, checked);
                            panel.rebuild++;
                        }
                    }
                    TapHandler { onTapped: panel.toggleGroup(rowItem.modelData.key) }
                }

                Item {
                    id: objRow
                    visible: rowItem.modelData.kind === "obj"
                    anchors.fill: parent
                    property var o: rowItem.modelData.o || ({})
                    property string oName: String(o.name || o.objid || "")
                    property string oId: String(o.objid || "")
                    property bool oSkipped: o.skipped === true
                    opacity: oSkipped ? 0.55 : 1
                    Rectangle {
                        anchors.fill: parent
                        color: rowHover.hovered ? Theme.hoverWash : "transparent"
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1
                                color: Theme.border }
                    Row {
                        anchors.left: parent.left
                        anchors.leftMargin: (rowItem.modelData.depth || 0) > 0 ? 19 : Theme.spacingMd
                        anchors.right: objSwitch.left
                        anchors.rightMargin: Theme.spacingSm
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 9
                        Label {
                            width: 22
                            text: objRow.oId
                            color: Theme.textTertiary
                            font.pixelSize: Theme.fontMicro
                            font.family: Theme.monoFamily
                            elide: Text.ElideRight
                        }
                        Label {
                            text: objRow.oName
                            color: Theme.textMutedBody
                            font.pixelSize: Theme.fontMeta
                            font.family: Theme.monoFamily
                            elide: Text.ElideRight
                        }
                    }
                    ThemedSwitch {
                        id: objSwitch
                        anchors.right: parent.right
                        anchors.rightMargin: -((width - pillWidth) / 2)
                        anchors.verticalCenter: parent.verticalCenter
                        pillWidth: 26; pillHeight: 15
                        checked: !objRow.oSkipped
                        onToggled: { editor.setObjectSkipped(objRow.oId, !checked); panel.rebuild++; }
                    }
                    HoverHandler { id: rowHover }
                }
            }
        }
    }
}
