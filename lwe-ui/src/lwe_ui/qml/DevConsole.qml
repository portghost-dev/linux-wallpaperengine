import QtQuick
import QtQuick.Controls.Basic
import "."

// Console + Instruments column. The console is one read-only text surface over the session
// buffer the backend holds; the picker and filter only decide what is shown. Lines never
// wrap: the surface scrolls both ways. A copy yields the raw lines, not the stripped ones.
Item {
    id: col

    property int rev: 0
    property int padV: 14
    property int padH: 18

    property int source: 2
    property string filter: ""

    // the buffer mirror and the lines on the surface, in surface order
    property var entries: []
    property var shown: []
    readonly property real rowH: shown.length > 0 ? logText.contentHeight / shown.length : 0

    function lineCount(side) {
        var n = 0;
        for (var i = 0; i < entries.length; i++)
            if (side === undefined || entries[i].src === side)
                n++;
        return n;
    }

    function shows(src) {
        if (col.source === 3) return src === "D";
        if (col.source === 2) return src === "A" || src === "B";
        return src === (col.source === 0 ? "A" : "B");
    }

    function passes(e) {
        return shows(e.src) && (col.filter === "" || e.text.toLowerCase().indexOf(col.filter) >= 0);
    }

    function thousands(n) {
        var s = String(n);
        var out = "";
        while (s.length > 3) {
            out = "," + s.slice(-3) + out;
            s = s.slice(0, -3);
        }
        return s + out;
    }

    function escapeHtml(t) {
        return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function tagColor(src) {
        return src === "A" ? Theme.accent : src === "B" ? Theme.textSecondary : Theme.textTertiary;
    }

    function html(e) {
        var body = e.err ? Qt.lighter(Theme.danger, 1.35) : Theme.textTertiary;
        return "<p style=\"line-height:180%; margin:0; white-space:pre" + (e.err ? "; margin-left:6px" : "") + "\">"
             + "<span style=\"color:" + tagColor(e.src) + "\">" + e.src + "</span> "
             + "<span style=\"color:" + Theme.textTertiary + "\">" + e.time + "</span> "
             + "<span style=\"color:" + body + "\">" + escapeHtml(e.text) + "</span></p>";
    }

    function rebuild() {
        entries = dev.consoleEntries();
        var keep = [];
        var parts = [];
        for (var i = 0; i < entries.length; i++)
            if (passes(entries[i])) {
                keep.push(entries[i]);
                parts.push(html(entries[i]));
            }
        shown = keep;
        logText.text = parts.join("");
        marks.refresh();
        Qt.callLater(col.toEnd);
    }

    function toEnd() {
        logFlick.contentY = Math.max(0, logFlick.contentHeight - logFlick.height);
    }

    function dropShown(n) {
        var all = logText.getText(0, 100000000);
        var pos = -1;
        for (var k = 0; k < n; k++) {
            pos = all.indexOf("\u2029", pos + 1);
            if (pos < 0)
                break;
        }
        if (pos >= 0)
            logText.remove(0, pos + 1);
        else
            logText.text = "";
    }

    function append(list, dropped) {
        var follow = logFlick.atYEnd || shown.length === 0;
        if (dropped > 0) {
            var gone = 0;
            for (var d = 0; d < dropped && d < entries.length; d++)
                if (passes(entries[d]))
                    gone++;
            entries = entries.slice(dropped);
            if (gone > 0) {
                shown = shown.slice(gone);
                dropShown(gone);
            }
        }
        var next = shown.slice();
        for (var i = 0; i < list.length; i++) {
            entries.push(list[i]);
            if (passes(list[i])) {
                next.push(list[i]);
                logText.append(html(list[i]));
            }
        }
        shown = next;
        marks.refresh();
        if (follow)
            Qt.callLater(col.toEnd);
    }

    // the raw lines under the selection, whole lines, in surface order
    function copySelection() {
        var a = logText.selectionStart, b = logText.selectionEnd;
        if (a === b || shown.length === 0)
            return;
        var first = logText.getText(0, a).split("\u2029").length - 1;
        var last = logText.getText(0, b).split("\u2029").length - 1;
        var lines = [];
        for (var i = first; i <= last && i < shown.length; i++)
            lines.push(shown[i].raw);
        dev.setClipboard(lines.join("\n"));
    }

    function showResidue(side) {
        col.source = side === "A" ? 0 : 1;
        sourceSeg.currentIndex = col.source;
        rebuild();
        for (var i = 0; i < shown.length; i++)
            if (shown[i].src === side && shown[i].residue === true) {
                Qt.callLater(function() { logFlick.contentY = Math.min(i * col.rowH,
                                              Math.max(0, logFlick.contentHeight - logFlick.height)); });
                return;
            }
    }

    onSourceChanged: rebuild()
    onFilterChanged: rebuild()
    Component.onCompleted: rebuild()

    Connections {
        target: dev
        function onConsoleLines(list, dropped) { col.append(list, dropped) }
        function onConsoleReset() { col.rebuild() }
        function onTailShown(side) { col.showResidue(side) }
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
            Label {
                id: consoleCount
                objectName: "devConsoleCount"
                anchors.left: consoleRuleLabel.right
                anchors.leftMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                text: col.thousands(dev.consoleCount) + " lines"
                font.pixelSize: 11
                color: Theme.textTertiary
            }
            Rectangle {
                anchors.left: consoleCount.right
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

            Flickable {
                id: logFlick
                objectName: "devConsoleFlick"
                anchors.fill: parent
                anchors.margins: 8
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                contentWidth: Math.max(width, logText.contentWidth + 2)
                contentHeight: Math.max(height, logText.contentHeight)
                onContentYChanged: marks.refresh()
                onHeightChanged: marks.refresh()

                // the surface scrolls; the bars ride the box padding, clear of the text
                ScrollBar.vertical: ScrollBar {
                    id: vbar
                    objectName: "devConsoleVBar"
                    parent: consoleBox
                    x: consoleBox.width - width - 3
                    y: 8
                    height: consoleBox.height - 16
                    padding: 0
                    policy: ScrollBar.AsNeeded
                    background: Item {}
                    contentItem: Rectangle {
                        implicitWidth: 4
                        radius: 2
                        color: Qt.rgba(1, 1, 1, 0.25)
                        opacity: vbar.active ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: 150 } }
                    }
                }
                ScrollBar.horizontal: ScrollBar {
                    id: hbar
                    objectName: "devConsoleHBar"
                    parent: consoleBox
                    x: 10
                    y: consoleBox.height - height - 2
                    width: consoleBox.width - 20
                    padding: 0
                    policy: ScrollBar.AsNeeded
                    background: Item {}
                    contentItem: Rectangle {
                        implicitHeight: 4
                        radius: 2
                        color: Qt.rgba(1, 1, 1, 0.25)
                        opacity: hbar.active ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: 150 } }
                    }
                }

                Item {
                    id: marks
                    objectName: "devConsoleMarks"
                    width: 2
                    height: logText.contentHeight
                    property var rows: []
                    function refresh() {
                        if (col.rowH <= 0 || col.shown.length === 0) {
                            rows = [];
                            return;
                        }
                        var first = Math.max(0, Math.floor(logFlick.contentY / col.rowH));
                        var last = Math.min(col.shown.length - 1,
                                            Math.ceil((logFlick.contentY + logFlick.height) / col.rowH));
                        var out = [];
                        for (var i = first; i <= last; i++)
                            if (col.shown[i].err)
                                out.push(i);
                        rows = out;
                    }
                    Repeater {
                        model: marks.rows
                        delegate: Rectangle {
                            required property int modelData
                            x: 0
                            y: modelData * col.rowH
                            width: 2
                            height: col.rowH
                            color: Theme.danger
                        }
                    }
                }

                TextEdit {
                    id: logText
                    objectName: "devConsoleText"
                    width: logFlick.contentWidth
                    readOnly: true
                    selectByMouse: true
                    selectByKeyboard: true
                    persistentSelection: true
                    textFormat: TextEdit.RichText
                    wrapMode: TextEdit.NoWrap
                    font.pixelSize: Theme.fontMicro - 1
                    font.family: Theme.monoFamily
                    color: Theme.textTertiary
                    Keys.onPressed: function(event) {
                        if (event.matches(StandardKey.Copy)) {
                            col.copySelection();
                            event.accepted = true;
                        }
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
