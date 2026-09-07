import QtQuick
import QtQuick.Controls.Basic
import "."

Popup {
    id: modal

    // sec 3 modal guard: every face is already <=620 and none was width-responsive, so
    // below a 660 window they clamp to width-24 (12px side margins) instead of
    // overhanging. Theme.usableWidth excludes the rail; modals center on the WINDOW,
    // so the rail's 64px is added back before comparing.
    width: Math.min(460, Theme.usableWidth + 64 - 24)
    modal: true
    anchors.centerIn: parent
    padding: 0

    background: Rectangle {
        color: Theme.surface
        radius: Theme.radiusMd
        border.width: 1
        border.color: Theme.borderStrong
    }
    Overlay.modal: Rectangle { color: Theme.scrimPlate }

    property bool schedEnabled: false
    property var plModel: []       // [{slug, name, ...}]
    property string slugA: ""
    property string slugB: ""

    function nameIndex(slug) {
        for (var i = 0; i < plModel.length; i++)
            if (plModel[i].slug === slug) return i;
        return 0;
    }
    function toMin(t) {
        var m = /^(\d{2}):(\d{2})$/.exec(t);
        if (!m) return -1;
        return parseInt(m[1]) * 60 + parseInt(m[2]);
    }
    function fmtMin(v) {
        var h = Math.floor(v / 60), m = v % 60;
        return (h < 10 ? "0" + h : String(h)) + ":" + (m < 10 ? "0" + m : String(m));
    }

    onOpened: {
        plModel = backend.playlistList();
        schedEnabled = backend.getSetting("SCHEDULE_ENABLED") === true;
        var sched = String(backend.getSetting("SCHEDULE") || "");
        var parts = sched.split(";");
        var e1 = (parts[0] || "").split("=");
        var e2 = (parts[1] || "").split("=");
        entryA.time.text = e1.length === 2 ? e1[0] : "08:00";
        slugA = e1.length === 2 ? e1[1] : (plModel[0] ? plModel[0].slug : "");
        entryB.time.text = e2.length === 2 ? e2[0] : "20:00";
        slugB = e2.length === 2 ? e2[1] : (plModel[0] ? plModel[0].slug : "");
        entryA.combo.currentIndex = nameIndex(slugA);
        entryB.combo.currentIndex = nameIndex(slugB);
    }

    contentItem: Column {
        spacing: 0

        Item {
            width: parent.width
            height: 40
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: Theme.border
            }
            Row {
                anchors.left: parent.left
                anchors.leftMargin: Theme.spacingLg
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spacingSm
                Label {
                    text: "Schedule"
                    color: Theme.textPrimary
                    font.pixelSize: 14
                    font.weight: Theme.weightMedium
                }
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "two playlists, one changeover"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMeta
                }
            }
            Item {
                id: closeBtn
                anchors.right: parent.right
                anchors.rightMargin: Theme.spacingSm
                anchors.verticalCenter: parent.verticalCenter
                width: 24; height: 24
                IconX {
                    anchors.centerIn: parent
                    size: 11
                    color: Theme.textSecondary
                }
                HoverHandler { id: closeHover }
                Rectangle {
                    anchors.fill: parent
                    z: -1
                    radius: Theme.radiusXs
                    color: closeHover.hovered ? Theme.hoverWash : "transparent"
                }
                TapHandler { onTapped: modal.close() }
            }
        }

        Column {
            width: parent.width
            leftPadding: Theme.spacingLg
            rightPadding: Theme.spacingLg
            topPadding: Theme.spacingMd
            bottomPadding: Theme.spacingLg
            spacing: Theme.spacingMd

            SRow {
                label: "Enable schedule"
                ThemedSwitch {
                    checked: modal.schedEnabled
                    onToggled: modal.schedEnabled = checked
                }
            }

            component EntryRow: Row {
                id: er
                property alias combo: comboAlias
                property alias time: timeAlias
                property color dotColor: Theme.accent
                property bool sun: false
                width: parent.width - parent.leftPadding - parent.rightPadding
                spacing: Theme.spacingSm
                IconSun {
                    objectName: "entrySun"
                    visible: er.sun
                    size: 14
                    anchors.verticalCenter: parent.verticalCenter
                    color: er.dotColor
                }
                IconMoon {
                    objectName: "entryMoon"
                    visible: !er.sun
                    size: 14
                    anchors.verticalCenter: parent.verticalCenter
                    color: er.dotColor
                }
                ThemedCombo {
                    id: comboAlias
                    width: er.width - 14 - fromLbl.implicitWidth - timeAlias.width - er.spacing * 3
                    height: 28
                    model: modal.plModel.map(function(p) { return p.name; })
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.inputWell
                        border.width: 1
                        border.color: comboAlias.activeFocus ? Theme.accent : Theme.border
                    }
                }
                Label {
                    id: fromLbl
                    anchors.verticalCenter: parent.verticalCenter
                    text: "from"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontControl
                }
                TextField {
                    id: timeAlias
                    objectName: "timeField"   // stable test hook, scoped per EntryRow parent
                    width: 74
                    height: 28
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontControl
                    font.family: Theme.monoFamily
                    horizontalAlignment: Text.AlignHCenter
                    selectByMouse: true
                    validator: RegularExpressionValidator { regularExpression: /^\d{2}:\d{2}$/ }
                    background: Rectangle {
                        color: Theme.inputWell
                        radius: Theme.radiusSm
                        border.width: 1
                        border.color: timeAlias.activeFocus ? Theme.borderStrong : Theme.border
                    }
                }
            }

            EntryRow { id: entryA; objectName: "entryA"; dotColor: Theme.warning; sun: true }
            EntryRow { id: entryB; objectName: "entryB"; dotColor: Theme.accent }

            // 24h strip: two spans and one handle per entry. Colours bind to the ENTRY that owns
            // each boundary, not to the sorted numbers, so the association survives entry B's
            // time landing earlier in the day than entry A's. A handle drags its entry's time.
            Item {
                id: dayStripRow
                objectName: "dayStripRow"   // stable test hook (findChild reaches objectName, not id)
                width: parent.width - parent.leftPadding - parent.rightPadding
                height: 26

                property int a: modal.toMin(entryA.time.text)
                property int b: modal.toMin(entryB.time.text)
                property bool ok: a >= 0 && b >= 0 && a !== b
                property bool aIsEarlier: a < b
                property int loMin: aIsEarlier ? a : b
                property int hiMin: aIsEarlier ? b : a
                property color loColor: aIsEarlier ? entryA.dotColor : entryB.dotColor
                property color hiColor: aIsEarlier ? entryB.dotColor : entryA.dotColor
                readonly property int barY: 9
                readonly property int barH: 7

                // a drag snaps to the quarter hour; the field takes any minute typed
                readonly property int snap: 15
                function minuteAt(px) {
                    var m = Math.round(px / width * 1440 / snap) * snap;
                    return Math.max(0, Math.min(1440 - snap, m));
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    y: parent.barY
                    height: parent.barH
                    radius: height / 2
                    color: Theme.border
                }
                Rectangle {
                    // middle span: loMin -> hiMin, owned by the earlier boundary's entry
                    visible: parent.ok
                    x: parent.width * (parent.loMin / 1440)
                    y: parent.barY
                    width: parent.width * ((parent.hiMin - parent.loMin) / 1440)
                    height: parent.barH
                    color: parent.loColor
                    opacity: 0.8
                }
                Rectangle {
                    // tail span: hiMin -> midnight, owned by the later boundary's entry
                    visible: parent.ok
                    x: parent.width * (parent.hiMin / 1440)
                    y: parent.barY
                    width: parent.width * ((1440 - parent.hiMin) / 1440)
                    height: parent.barH
                    radius: height / 2
                    color: parent.hiColor
                    opacity: 0.8
                }
                Rectangle {
                    // wraparound head: midnight -> loMin, same entry as the tail (carries over midnight)
                    visible: parent.ok
                    x: 0
                    y: parent.barY
                    width: parent.width * (parent.loMin / 1440)
                    height: parent.barH
                    radius: height / 2
                    color: parent.hiColor
                    opacity: 0.8
                }

                component TimeHandle: Item {
                    id: handle
                    property var entry           // the EntryRow whose time this handle moves
                    property int minute: 0
                    objectName: "timeHandle"
                    visible: dayStripRow.ok
                    width: 18
                    height: dayStripRow.height
                    x: dayStripRow.width * (minute / 1440) - width / 2
                    y: 0
                    z: 2
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: 3
                        width: 4
                        height: 20
                        radius: 2
                        color: Theme.textPrimary
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.SizeHorCursor
                        preventStealing: true
                        onPositionChanged: function(mouse) {
                            if (!pressed) return;
                            var px = handle.x + handle.width / 2 + (mouse.x - handle.width / 2);
                            handle.entry.time.text = modal.fmtMin(dayStripRow.minuteAt(px));
                        }
                    }
                }
                TimeHandle { objectName: "handleA"; entry: entryA; minute: dayStripRow.a }
                TimeHandle { objectName: "handleB"; entry: entryB; minute: dayStripRow.b }
            }
            Item {
                // the times sit under their own positions on the strip; midnight at both ends.
                // Two times close together stack their labels instead of colliding.
                id: stripLabels
                width: parent.width - parent.leftPadding - parent.rightPadding
                readonly property real xA: Math.max(0, Math.min(width - labelA.width, width * (dayStripRow.a / 1440) - labelA.width / 2))
                readonly property real xB: Math.max(0, Math.min(width - labelB.width, width * (dayStripRow.b / 1440) - labelB.width / 2))
                readonly property bool collide: dayStripRow.ok && Math.abs(xA - xB) < Math.max(labelA.width, labelB.width) + 4
                height: collide ? 28 : 14
                Label {
                    x: 0
                    text: "00:00"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMicro
                }
                Label {
                    id: labelA
                    objectName: "labelA"
                    visible: dayStripRow.ok
                    x: stripLabels.xA
                    text: modal.fmtMin(dayStripRow.a)
                    color: entryA.dotColor
                    font.pixelSize: Theme.fontMicro
                }
                Label {
                    id: labelB
                    objectName: "labelB"
                    visible: dayStripRow.ok
                    x: stripLabels.xB
                    y: stripLabels.collide ? 14 : 0
                    text: modal.fmtMin(dayStripRow.b)
                    color: entryB.dotColor
                    font.pixelSize: Theme.fontMicro
                }
                Label {
                    x: parent.width - width
                    text: "00:00"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMicro
                }
            }

            Label {
                width: parent.width - parent.leftPadding - parent.rightPadding
                text: "Changeover happens at the next rotation tick, not mid-wallpaper. " +
                      "Manual playlist switches override until the next boundary."
                color: Theme.textTertiary
                font.pixelSize: Theme.fontMeta
                wrapMode: Text.WordWrap
            }

            Item {
                width: parent.width - parent.leftPadding - parent.rightPadding
                height: 1
                Rectangle { anchors.fill: parent; color: Theme.border }
            }

            Row {
                anchors.right: parent.right
                anchors.rightMargin: parent.rightPadding
                spacing: Theme.spacingSm
                topPadding: Theme.spacingXs
                Button {
                    id: cancelBtn
                    text: "Cancel"
                    onClicked: modal.close()
                    contentItem: Label {
                        text: cancelBtn.text
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontControl
                        horizontalAlignment: Text.AlignHCenter
                    }
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: cancelBtn.hovered ? Theme.hoverWash : "transparent"
                        border.width: 1
                        border.color: Theme.border
                    }
                }
                Button {
                    id: saveBtn
                    text: "Save"
                    onClicked: {
                        var ta = entryA.time.text, tb = entryB.time.text;
                        if (modal.toMin(ta) < 0 || modal.toMin(tb) < 0)
                            return;
                        var sa = modal.plModel[entryA.combo.currentIndex];
                        var sb = modal.plModel[entryB.combo.currentIndex];
                        if (!sa || !sb)
                            return;
                        backend.setSetting("SCHEDULE", ta + "=" + sa.slug + ";" + tb + "=" + sb.slug);
                        backend.setSetting("SCHEDULE_ENABLED", modal.schedEnabled);
                        modal.close();
                    }
                    contentItem: Label {
                        text: saveBtn.text
                        color: Theme.onAccent
                        font.pixelSize: Theme.fontControl
                        horizontalAlignment: Text.AlignHCenter
                    }
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.accent
                    }
                }
            }
        }
    }
}
