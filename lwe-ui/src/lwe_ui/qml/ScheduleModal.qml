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
                width: parent.width - parent.leftPadding - parent.rightPadding
                spacing: Theme.spacingSm
                Rectangle {
                    width: 6; height: 6; radius: 3
                    anchors.verticalCenter: parent.verticalCenter
                    color: er.dotColor
                }
                ThemedCombo {
                    id: comboAlias
                    width: er.width - 6 - fromLbl.implicitWidth - timeAlias.width - er.spacing * 3
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
                    validator: RegularExpressionValidator { regularExpression: /^\d{2}:\d{2}$/ }
                    background: Rectangle {
                        color: Theme.inputWell
                        radius: Theme.radiusSm
                        border.width: 1
                        border.color: timeAlias.activeFocus ? Theme.borderStrong : Theme.border
                    }
                }
            }

            EntryRow { id: entryA; objectName: "entryA"; dotColor: Theme.warning }
            EntryRow { id: entryB; objectName: "entryB"; dotColor: Theme.accent }

            // 24h strip with the two spans + boundary ticks. Colors bind to whichever ENTRY
            // owns each boundary (not to the sorted lo/hi numbers) so the association survives
            // entry B's time landing earlier in the day than entry A's (F25 fix).
            Item {
                id: dayStripRow
                objectName: "dayStripRow"   // stable test hook (findChild reaches objectName, not id)
                width: parent.width - parent.leftPadding - parent.rightPadding
                height: 14

                property int a: modal.toMin(entryA.time.text)
                property int b: modal.toMin(entryB.time.text)
                property bool ok: a >= 0 && b >= 0 && a !== b
                // the earlier boundary owns the span that runs from it to the later boundary;
                // the later boundary owns the wraparound span (itself -> midnight -> the
                // earlier boundary), since that entry's assignment carries over past 24:00.
                property bool aIsEarlier: a < b
                property int loMin: aIsEarlier ? a : b
                property int hiMin: aIsEarlier ? b : a
                property color loColor: aIsEarlier ? entryA.dotColor : entryB.dotColor
                property color hiColor: aIsEarlier ? entryB.dotColor : entryA.dotColor

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    y: 6
                    height: 2
                    radius: 1
                    color: Theme.border
                }
                Rectangle {
                    // middle span: loMin -> hiMin, owned by the earlier boundary's entry
                    visible: parent.ok
                    x: parent.width * (parent.loMin / 1440)
                    y: 6
                    width: parent.width * ((parent.hiMin - parent.loMin) / 1440)
                    height: 2
                    radius: 1
                    color: parent.loColor
                    opacity: 0.8
                }
                Rectangle {
                    // tail span: hiMin -> 24:00, owned by the later boundary's entry
                    visible: parent.ok
                    x: parent.width * (parent.hiMin / 1440)
                    y: 6
                    width: parent.width * ((1440 - parent.hiMin) / 1440)
                    height: 2
                    radius: 1
                    color: parent.hiColor
                    opacity: 0.8
                }
                Rectangle {
                    // wraparound head: 00:00 -> loMin, same entry as the tail (carries over midnight)
                    visible: parent.ok
                    x: 0
                    y: 6
                    width: parent.width * (parent.loMin / 1440)
                    height: 2
                    radius: 1
                    color: parent.hiColor
                    opacity: 0.8
                }
                Rectangle {
                    visible: parent.ok
                    x: parent.width * (parent.loMin / 1440) - 0.75
                    y: 2
                    width: 1.5
                    height: 10
                    color: Theme.textPrimary
                }
                Rectangle {
                    visible: parent.ok
                    x: parent.width * (parent.hiMin / 1440) - 0.75
                    y: 2
                    width: 1.5
                    height: 10
                    color: Theme.textPrimary
                }
            }
            Row {
                width: parent.width - parent.leftPadding - parent.rightPadding
                Label {
                    width: parent.width / 4
                    text: "00:00"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMicro
                    horizontalAlignment: Text.AlignLeft
                }
                Label {
                    width: parent.width / 4
                    text: dayStripRow.ok ? modal.fmtMin(dayStripRow.loMin) : "--:--"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMicro
                    horizontalAlignment: Text.AlignHCenter
                }
                Label {
                    width: parent.width / 4
                    text: dayStripRow.ok ? modal.fmtMin(dayStripRow.hiMin) : "--:--"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMicro
                    horizontalAlignment: Text.AlignHCenter
                }
                Label {
                    width: parent.width / 4
                    text: "24:00"
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMicro
                    horizontalAlignment: Text.AlignRight
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
