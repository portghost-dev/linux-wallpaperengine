import QtQuick
import QtQuick.Controls.Basic
import "."

// Console + Instruments column. The console keeps every line tagged by source; the picker
// and filter only decide what is shown. stderr lines carry the danger mark.
Item {
    id: col

    property int rev: 0
    property int padV: 14
    property int padH: 18

    property int source: 2
    property string filter: ""

    function replayTail(side) {
        var lines = dev.tailLines(side);
        var st = dev.slotState(side);
        if (lines.length === 0 && st.lastCode < 0)
            return;
        logModel.append({src: side, text: "Last run · exit " + st.lastCode + " · " + (st.lastTs || ""), err: false});
        for (var i = 0; i < lines.length; i++)
            logModel.append({src: side, text: lines[i].text, err: lines[i].err === true});
        col.trim();
        Qt.callLater(logView.positionViewAtEnd);
    }

    function lineCount(side) {
        var n = 0;
        for (var i = 0; i < logModel.count; i++)
            if (side === undefined || logModel.get(i).src === side)
                n++;
        return n;
    }

    function clearSide(side) {
        for (var i = logModel.count - 1; i >= 0; i--)
            if (logModel.get(i).src === side)
                logModel.remove(i);
    }

    Component.onCompleted: { replayTail("A"); replayTail("B"); }

    function trim() {
        if (logModel.count > 2000)
            logModel.remove(0, logModel.count - 1800);
    }

    function shows(src) {
        if (col.source === 3) return src === "D";
        if (col.source === 2) return src === "A" || src === "B";
        return src === (col.source === 0 ? "A" : "B");
    }

    ListModel { id: logModel }

    Connections {
        target: dev
        function onRunStarted(side) { col.clearSide(side) }
        function onConsoleLine(src, line, err) {
            var follow = logView.atYEnd || logModel.count === 0;
            logModel.append({src: src, text: line, err: err});
            col.trim();
            if (follow)
                Qt.callLater(logView.positionViewAtEnd);
        }
    }

    Column {
        anchors.fill: parent
        anchors.margins: col.padV
        anchors.leftMargin: col.padH
        anchors.rightMargin: col.padH
        spacing: 0

        Item {
            width: parent.width
            height: 24
            Label {
                id: consoleRuleLabel
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "Console"
                font.pixelSize: 11
                font.weight: Theme.weightMedium
                color: Theme.textSecondary
            }
            Rectangle {
                anchors.left: consoleRuleLabel.right
                anchors.leftMargin: 8
                anchors.right: sourceSeg.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                height: 1
                color: Theme.hairline
            }
            SegmentControl {
                id: sourceSeg
                objectName: "devConsoleSource"
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                sizeClass: "h22"
                model: ["A", "B", "Both", "Daemon"]
                currentIndex: col.source
                onActivated: function(i) { col.source = i }
            }
        }

        Item { width: 1; height: 8 }

        TextField {
            id: filterField
            width: parent.width
            height: 22
            placeholderText: "filter"
            placeholderTextColor: Theme.textTertiary
            color: Theme.textPrimary
            font.pixelSize: 10
            font.family: Theme.monoFamily
            leftPadding: 8
            selectByMouse: true
            background: Rectangle {
                radius: 5
                color: Theme.inputWell
                border.width: 1
                border.color: filterField.activeFocus ? Theme.borderStrong : Theme.border
            }
            onTextEdited: col.filter = text.toLowerCase()
        }

        Item { width: 1; height: 8 }

        Rectangle {
            id: consoleBox
            objectName: "devConsoleBox"
            width: parent.width
            height: Math.max(0, (parent.height - y - 12 - 24 - 2) / 2)
            color: Theme.base
            radius: Theme.radiusSm
            border.width: 1
            border.color: Theme.hairline
            clip: true

            ListView {
                id: logView
                anchors.fill: parent
                anchors.margins: 8
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: logModel
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: Item {
                    id: line
                    required property string src
                    required property string text
                    required property bool err
                    readonly property bool shown: col.shows(line.src)
                                                  && (col.filter === "" || line.text.toLowerCase().indexOf(col.filter) >= 0)
                    width: logView.width
                    height: shown ? Math.round(9.5 * 1.8) : 0
                    visible: shown
                    Rectangle {
                        visible: line.err
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: 2
                        color: Theme.danger
                    }
                    Label {
                        anchors.left: parent.left
                        anchors.leftMargin: line.err ? 6 : 0
                        anchors.verticalCenter: parent.verticalCenter
                        width: 14
                        text: line.src
                        font.pixelSize: Theme.fontMicro - 1
                        font.family: Theme.monoFamily
                        color: line.src === "A" ? Theme.accent : line.src === "B" ? Theme.textSecondary : Theme.textTertiary
                    }
                    Label {
                        anchors.left: parent.left
                        anchors.leftMargin: (line.err ? 6 : 0) + 16
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: line.text
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        font.pixelSize: Theme.fontMicro - 1
                        font.family: Theme.monoFamily
                        color: line.err ? Qt.lighter(Theme.danger, 1.35) : Theme.textTertiary
                    }
                }
            }
        }

        Item { width: 1; height: 12 }

        PRule { label: "Instruments" }

        Item { width: 1; height: 2 }

        Item {
            width: parent.width
            height: Math.max(0, parent.height - y)

            Flickable {
                id: instFlick
                anchors.fill: parent
                anchors.rightMargin: -col.padH
                contentHeight: instGrid.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                Row {
                    id: instGrid
                    objectName: "devInstrumentGrid"
                    width: instFlick.width - col.padH - 12
                    spacing: 16

                    readonly property var rows: {
                        col.rev;
                        var all = dev.instruments();
                        var rows = [];
                        for (var i = 0; i < all.length; i++)
                            rows.push({kind: "instrument", key: all[i].env, label: all[i].name,
                                       tip: all[i].tip, live: all[i].live});
                        var half = Math.ceil(rows.length / 2);
                        return [rows.slice(0, half), rows.slice(half)];
                    }

                    Repeater {
                        model: 2
                        delegate: DevToggleColumn {
                            required property int index
                            width: (instGrid.width - instGrid.spacing) / 2
                            rows: instGrid.rows[index]
                            rev: col.rev
                            mono: true
                            nameSize: 11
                            rowH: 28
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
