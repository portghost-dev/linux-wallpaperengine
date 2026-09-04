import QtQuick
import QtQuick.Controls.Basic
import "."

// Isolator column: the editor's Object Exclusion face with a switch per side. Session
// scoped: live over the exhibit's socket while it runs, applied at launch otherwise.
Item {
    id: iso

    property int rev: 0
    property int padV: 14
    property int padH: 18
    property int cellW: 38

    property bool treeMode: false
    property string search: ""
    property string typeFilter: "all"
    property var collapsed: ({})
    property int rebuild: 0

    readonly property var typeChoices: [
        {label: "All", value: "all"}, {label: "Particles", value: "particle"},
        {label: "Sounds", value: "sound"}, {label: "Effects", value: "effect"},
        {label: "Lights", value: "light"}, {label: "Images", value: "image"},
        {label: "Models", value: "model"}, {label: "Text", value: "text"}
    ]

    readonly property bool liveA: (rev, dev.isolatorEditable("A"))
    readonly property bool liveB: (rev, dev.isolatorEditable("B"))

    // objects merged by id across both sides; each row knows its per-side state
    readonly property var objs: {
        rev;
        var a = dev.objectList("A");
        var b = dev.objectList("B");
        var byId = {};
        var out = [];
        for (var i = 0; i < a.length; i++) {
            var o = {objid: a[i].objid, name: a[i].name, type: a[i].type, parent: a[i].parent,
                     inA: true, onA: a[i].on, inB: false, onB: false};
            byId[o.objid] = o; out.push(o);
        }
        for (var j = 0; j < b.length; j++) {
            var e = byId[b[j].objid];
            if (e) { e.inB = true; e.onB = b[j].on; }
            else out.push({objid: b[j].objid, name: b[j].name, type: b[j].type, parent: b[j].parent,
                           inA: false, onA: false, inB: true, onB: b[j].on});
        }
        return out;
    }

    readonly property bool treeAvailable: {
        for (var i = 0; i < objs.length; i++)
            if (objs[i].parent !== "") return true;
        return false;
    }

    function accepts(o) {
        if (typeFilter !== "all" && o.type !== typeFilter) return false;
        if (search !== "") {
            var s = search.toLowerCase();
            if ((String(o.name) + " " + String(o.objid)).toLowerCase().indexOf(s) < 0) return false;
        }
        return true;
    }

    readonly property var filtered: (rebuild, objs.filter(accepts))

    function isCollapsed(k) { return iso.collapsed[k] === true; }
    function toggleGroup(k) {
        var c = iso.collapsed;
        c[k] = !(c[k] === true);
        iso.collapsed = c;
        iso.rebuild++;
    }

    function allOn(side, list) {
        for (var i = 0; i < list.length; i++) {
            var o = list[i];
            if (side === "A" && o.inA && !o.onA) return false;
            if (side === "B" && o.inB && !o.onB) return false;
        }
        return list.length > 0;
    }

    function idsFor(side, list) {
        var ids = [];
        for (var i = 0; i < list.length; i++)
            if ((side === "A" && list[i].inA) || (side === "B" && list[i].inB))
                ids.push(list[i].objid);
        return ids;
    }

    component SideCell: Item {
        id: sc
        property string side: "A"
        property bool present: true
        property bool checked: false
        signal flipped(bool on)
        width: iso.cellW
        height: parent ? parent.height : 28
        readonly property bool live: sc.side === "A" ? iso.liveA : iso.liveB
        ThemedSwitch {
            anchors.centerIn: parent
            pillWidth: 28
            pillHeight: 16
            visible: sc.present
            enabled: sc.live
            opacity: sc.live ? 1 : 0.55
            checked: sc.checked
            onToggled: sc.flipped(checked)
        }
    }

    Column {
        anchors.fill: parent
        anchors.margins: iso.padV
        anchors.leftMargin: iso.padH
        anchors.rightMargin: iso.padH
        spacing: 0

        PRule { label: "Isolator" }

        Item { width: 1; height: 5 }

        Item {
            width: parent.width
            height: 22
            SegmentControl {
                id: modeSeg
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                sizeClass: "h22"
                model: ["Groups", "Tree"]
                currentIndex: iso.treeMode ? 1 : 0
                onActivated: function(i) { iso.treeMode = (i === 1); iso.rebuild++; }
            }
            TextField {
                id: searchField
                anchors.left: modeSeg.right
                anchors.leftMargin: 6
                anchors.right: typeDrop.left
                anchors.rightMargin: 6
                anchors.verticalCenter: parent.verticalCenter
                height: 22
                placeholderText: "search"
                placeholderTextColor: Theme.textTertiary
                color: Theme.textPrimary
                font.pixelSize: Theme.fontMicro
                font.family: Theme.monoFamily
                leftPadding: 8
                selectByMouse: true
                background: Rectangle {
                    radius: 5
                    color: Theme.inputWell
                    border.width: 1
                    border.color: searchField.activeFocus ? Theme.borderStrong : Theme.border
                }
                onTextEdited: { iso.search = text; iso.rebuild++; }
            }
            Rectangle {
                id: typeDrop
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                width: 84
                height: 22
                radius: 5
                color: Theme.inputWell
                border.width: 1
                border.color: Theme.border
                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    anchors.right: typeCaret.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: {
                        for (var i = 0; i < iso.typeChoices.length; i++)
                            if (iso.typeChoices[i].value === iso.typeFilter)
                                return iso.typeChoices[i].label;
                        return "All";
                    }
                    elide: Text.ElideRight
                    font.pixelSize: Theme.fontMicro
                    color: Theme.textPrimary
                }
                IconChevron {
                    id: typeCaret
                    anchors.right: parent.right
                    anchors.rightMargin: 7
                    anchors.verticalCenter: parent.verticalCenter
                    direction: "down"
                    size: 10
                    color: Theme.textSecondary
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: typeMenu.visible ? typeMenu.close() : typeMenu.open() }
                Menu {
                    id: typeMenu
                    parent: typeDrop
                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                    y: typeDrop.height + 2
                    background: Rectangle {
                        implicitWidth: 120
                        color: Theme.surfaceVariant
                        radius: Theme.radiusSm
                        border.width: 1
                        border.color: Theme.borderStrong
                    }
                    Repeater {
                        model: iso.typeChoices
                        delegate: ThemedMenuItem {
                            required property var modelData
                            text: modelData.label
                            onTriggered: { iso.typeFilter = modelData.value; iso.rebuild++; }
                        }
                    }
                }
            }
        }

        Item {
            id: masters
            objectName: "devIsolatorMasters"
            width: parent.width
            height: 28
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hairline }
            Label {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "All filtered"
                font.pixelSize: Theme.fontMicro
                color: Theme.textSecondary
            }
            Row {
                anchors.right: parent.right
                height: parent.height
                SideCell {
                    side: "A"
                    checked: iso.allOn("A", iso.filtered)
                    onFlipped: function(on) { dev.setObjectsOn("A", iso.idsFor("A", iso.filtered), on) }
                }
                SideCell {
                    side: "B"
                    checked: iso.allOn("B", iso.filtered)
                    onFlipped: function(on) { dev.setObjectsOn("B", iso.idsFor("B", iso.filtered), on) }
                }
            }
        }

        Item {
            width: parent.width
            height: 16
            Row {
                anchors.right: parent.right
                Repeater {
                    model: ["A", "B"]
                    delegate: Item {
                        required property var modelData
                        width: iso.cellW
                        height: 16
                        Label {
                            anchors.centerIn: parent
                            text: parent.modelData
                            font.pixelSize: 10
                            color: Theme.textTertiary
                        }
                    }
                }
            }
        }

        Item {
            width: parent.width
            height: Math.max(0, parent.height - y)

            ListView {
                id: objList
                objectName: "devIsolatorList"
                anchors.fill: parent
                anchors.rightMargin: -iso.padH
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                property real keptY: 0
                onModelChanged: Qt.callLater(function() { objList.contentY = Math.min(objList.keptY, Math.max(0, objList.contentHeight - objList.height)) })
                onContentYChanged: if (!moving && contentY >= 0) keptY = contentY

                model: {
                    iso.rebuild;
                    var rows = [];
                    var filtered = iso.filtered;
                    if (iso.treeMode && iso.treeAvailable) {
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
                        var byGroup = {};
                        var order = [];
                        for (var j = 0; j < filtered.length; j++) {
                            var key = String(filtered[j].name || "") !== "" ? String(filtered[j].name)
                                                                            : String(filtered[j].type || "generic");
                            if (!byGroup[key]) { byGroup[key] = []; order.push(key); }
                            byGroup[key].push(filtered[j]);
                        }
                        for (var g = 0; g < order.length; g++) {
                            var items = byGroup[order[g]];
                            if (items.length === 1) {
                                rows.push({kind: "obj", depth: 0, o: items[0]});
                                continue;
                            }
                            rows.push({kind: "group", key: order[g], label: order[g], count: items.length,
                                       members: items, collapsed: iso.isCollapsed(order[g])});
                            if (!iso.isCollapsed(order[g]))
                                for (var m = 0; m < items.length; m++)
                                    rows.push({kind: "obj", depth: 1, o: items[m]});
                        }
                    }
                    return rows;
                }

                delegate: Item {
                    id: rowItem
                    required property var modelData
                    width: objList.width - iso.padH
                    height: 28

                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.hairlineFaint }

                    Rectangle {
                        visible: rowItem.modelData.depth === 1
                        x: 4
                        width: 1
                        height: parent.height
                        color: Theme.hairline
                    }

                    Item {
                        visible: rowItem.modelData.kind === "group"
                        anchors.left: parent.left
                        anchors.right: groupCells.left
                        height: parent.height
                        Item {
                            id: disclosure
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            width: 10; height: 10
                            Item {
                                anchors.centerIn: parent
                                width: 5; height: 5
                                rotation: rowItem.modelData.collapsed ? -45 : 45
                                Rectangle { anchors.right: parent.right; width: 5; height: 1.5; color: Theme.textTertiary }
                                Rectangle { anchors.bottom: parent.bottom; width: 1.5; height: 5; color: Theme.textTertiary }
                            }
                        }
                        Label {
                            anchors.left: disclosure.right
                            anchors.leftMargin: 8
                            anchors.right: groupCount.left
                            anchors.rightMargin: 6
                            anchors.verticalCenter: parent.verticalCenter
                            text: rowItem.modelData.label || ""
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            font.pixelSize: 11
                            font.family: Theme.monoFamily
                            color: Theme.textPrimary
                        }
                        Label {
                            id: groupCount
                            anchors.right: parent.right
                            anchors.rightMargin: 6
                            anchors.verticalCenter: parent.verticalCenter
                            text: rowItem.modelData.count || 0
                            font.pixelSize: 10
                            font.family: Theme.monoFamily
                            color: Theme.textTertiary
                        }
                        TapHandler { onTapped: iso.toggleGroup(rowItem.modelData.key) }
                    }
                    Row {
                        id: groupCells
                        visible: rowItem.modelData.kind === "group"
                        anchors.right: parent.right
                        height: parent.height
                        SideCell {
                            side: "A"
                            checked: rowItem.modelData.kind === "group" && iso.allOn("A", rowItem.modelData.members)
                            onFlipped: function(on) { dev.setObjectsOn("A", iso.idsFor("A", rowItem.modelData.members), on) }
                        }
                        SideCell {
                            side: "B"
                            checked: rowItem.modelData.kind === "group" && iso.allOn("B", rowItem.modelData.members)
                            onFlipped: function(on) { dev.setObjectsOn("B", iso.idsFor("B", rowItem.modelData.members), on) }
                        }
                    }

                    Item {
                        visible: rowItem.modelData.kind === "obj"
                        anchors.left: parent.left
                        anchors.leftMargin: rowItem.modelData.depth === 1 ? 12 : 0
                        anchors.right: objCells.left
                        height: parent.height
                        Label {
                            id: idLabel
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            width: 26
                            text: rowItem.modelData.o ? rowItem.modelData.o.objid : ""
                            font.pixelSize: 10
                            font.family: Theme.monoFamily
                            color: Theme.textTertiary
                            elide: Text.ElideRight
                        }
                        Label {
                            anchors.left: idLabel.right
                            anchors.leftMargin: 6
                            anchors.right: parent.right
                            anchors.rightMargin: 6
                            anchors.verticalCenter: parent.verticalCenter
                            text: rowItem.modelData.o ? rowItem.modelData.o.name : ""
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            font.pixelSize: 11
                            font.family: Theme.monoFamily
                            color: Theme.textPrimary
                        }
                    }
                    Row {
                        id: objCells
                        visible: rowItem.modelData.kind === "obj"
                        anchors.right: parent.right
                        height: parent.height
                        SideCell {
                            side: "A"
                            present: rowItem.modelData.o ? rowItem.modelData.o.inA : false
                            checked: rowItem.modelData.o ? rowItem.modelData.o.onA : false
                            onFlipped: function(on) { dev.setObjectOn("A", rowItem.modelData.o.objid, on) }
                        }
                        SideCell {
                            side: "B"
                            present: rowItem.modelData.o ? rowItem.modelData.o.inB : false
                            checked: rowItem.modelData.o ? rowItem.modelData.o.onB : false
                            onFlipped: function(on) { dev.setObjectOn("B", rowItem.modelData.o.objid, on) }
                        }
                    }
                }
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 22
                gradient: Gradient {
                    GradientStop { position: 0.0; color: Qt.rgba(Theme.base.r, Theme.base.g, Theme.base.b, 0) }
                    GradientStop { position: 1.0; color: Theme.base }
                }
            }
        }
    }
}
