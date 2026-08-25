import QtQuick
import QtQuick.Controls.Basic
import "."

Column {
    id: page

    property int trev: 0
    Connections { target: themeBridge; function onChanged() { page.trev++ } }

    spacing: Theme.spacingSm

    readonly property var roleRows: [
        { role: "background", label: "Background" },
        { role: "surface",    label: "Surface" },
        { role: "text",       label: "Text" },
        { role: "textMuted",  label: "Text muted" },
        { role: "accent",     label: "Accent" },
        { role: "border",     label: "Border" }
    ]
    property string editingRole: ""

    function _lum(c) { return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b; }

    Item {
        width: parent.width
        height: 34
        Label {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "Appearance"
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody13
        }
        Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.spacingSm

            Rectangle {
                id: presetPill
                width: 190; height: 26
                radius: Theme.radiusSm
                color: Theme.surface
                border.width: 1
                border.color: presetHover.hovered ? Theme.borderStrong : Theme.border
                anchors.verticalCenter: parent.verticalCenter
                Row {
                    anchors.left: parent.left
                    anchors.leftMargin: 9
                    anchors.right: parent.right
                    anchors.rightMargin: 9
                    anchors.verticalCenter: parent.verticalCenter
                    Label {
                        width: parent.width - 14
                        text: (page.trev, themeBridge ? themeBridge.activeName() : "")
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontControl
                        elide: Text.ElideRight
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    IconChevron { direction: "down"; size: 12; color: Theme.textTertiary
                                  anchors.verticalCenter: parent.verticalCenter }
                }
                HoverHandler { id: presetHover }
                TapHandler {
                    onTapped: {
                        if (presetMenu.visible) presetMenu.close();
                        else if (!presetMenu.justClosed) presetMenu.open();
                    }
                }
            }

            Rectangle {
                width: resetRow.implicitWidth + Theme.spacingLg; height: 26
                radius: Theme.radiusSm
                color: resetHov.hovered ? Theme.hoverWash : "transparent"
                border.width: 1; border.color: Theme.border
                anchors.verticalCenter: parent.verticalCenter
                Row {
                    id: resetRow
                    anchors.centerIn: parent
                    spacing: 5
                    Label { text: "↺"; color: Theme.textSecondary
                            font.pixelSize: Theme.fontControl
                            anchors.verticalCenter: parent.verticalCenter }
                    Label { text: "Reset"; color: Theme.textSecondary
                            font.pixelSize: Theme.fontMeta
                            anchors.verticalCenter: parent.verticalCenter }
                }
                HoverHandler { id: resetHov }
                TapHandler { onTapped: themeBridge.resetActive() }
            }
        }
    }

    Label {
        width: parent.width
        text: (page.trev, themeBridge ? themeBridge.activeBlurb() : "")
        color: Theme.textTertiary
        font.pixelSize: Theme.fontMeta
        wrapMode: Text.WordWrap
    }
    Label {
        width: parent.width
        visible: (page.trev, themeBridge ? themeBridge.borderFellBack() : false)
        text: "This theme's stored border is hard to read here, so outlines use the neutral hairline ladder instead."
        color: Theme.textTertiary
        font.pixelSize: Theme.fontMicro
        wrapMode: Text.WordWrap
    }

    Item { width: 1; height: Theme.spacingXs }

    Repeater {
        model: page.roleRows
        delegate: Item {
            id: roleRow
            required property var modelData
            width: page.width
            height: 34
            readonly property bool editing: page.editingRole === modelData.role
            // null-guarded: the context property drops during view teardown and a
            // type-strict color binding would spray TypeErrors on quit
            readonly property color cur: (page.trev,
                themeBridge ? themeBridge.roleValue(modelData.role) : "#000000")

            // editing wash: negative margin so the row text stays aligned (spec)
            Rectangle {
                anchors.fill: parent
                anchors.leftMargin: -Theme.spacingSm
                anchors.rightMargin: -Theme.spacingSm
                radius: Theme.radiusSm
                color: roleRow.editing ? Theme.selectionWash : "transparent"
            }
            Label {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: roleRow.modelData.label
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBody13
            }
            Row {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spacingSm

                TextField {
                    id: hexField
                    objectName: "themeHex_" + roleRow.modelData.role
                    width: 86; height: 24
                    readonly property real monoSize: 11.5
                    topPadding: 0; bottomPadding: 0; leftPadding: 8
                    color: Theme.textMutedBody
                    font.pixelSize: monoSize
                    font.family: Theme.monoFamily
                    anchors.verticalCenter: parent.verticalCenter
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.surface
                        border.width: 1
                        border.color: (roleRow.editing || hexField.activeFocus)
                                      ? Theme.accent : Theme.border
                    }
                    // reseed, never bind: a live binding would clobber typing (the
                    // established persistent-control pattern)
                    readonly property string bound: (page.trev,
                        themeBridge ? themeBridge.roleValue(roleRow.modelData.role) : "")
                    onBoundChanged: if (!activeFocus) text = bound
                    Component.onCompleted: text = bound
                    onAccepted: {
                        if (!themeBridge.setRoleLive(roleRow.modelData.role, text))
                            text = bound;
                        focus = false;
                    }
                    onActiveFocusChanged: if (!activeFocus && text !== bound) {
                        if (!themeBridge.setRoleLive(roleRow.modelData.role, text))
                            text = bound;
                    }
                }

                Rectangle {
                    width: 18; height: 18; radius: 5
                    anchors.verticalCenter: parent.verticalCenter
                    color: roleRow.cur
                    border.width: Math.abs(page._lum(roleRow.cur) - page._lum(Theme.base)) < 0.10 ? 1 : 0
                    border.color: Theme.ladderStrong
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: picker.openFor(roleRow.modelData.role, parent)
                    }
                }
            }
        }
    }

    Label {
        width: parent.width
        text: "Hex or RGB · edits save to the selected theme · Reset restores its " +
              "defaults. The remaining tones derive from these six."
        color: Theme.textTertiary
        font.pixelSize: Theme.fontMeta
        wrapMode: Text.WordWrap
    }

    Popup {
        id: presetMenu
        parent: page
        x: page.width - width
        y: 40
        width: 240
        height: Math.min(implicitContentHeight + 2, 392)
        padding: 1
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        property bool justClosed: false
        onClosed: { justClosed = true; presetGuard.restart() }
        Timer { id: presetGuard; interval: 150; onTriggered: presetMenu.justClosed = false }
        background: Rectangle {
            radius: Theme.radiusMd
            color: Theme.surfaceVariant
            border.width: 1
            border.color: Theme.borderStrong
        }
        contentItem: ListView {
            id: presetList
            clip: true
            implicitHeight: contentHeight
            model: presetMenu.visible ? (page.trev, themeBridge.themeList()) : []
            ScrollBar.vertical: ScrollBar {
                policy: presetList.contentHeight > presetList.height
                        ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                contentItem: Rectangle { implicitWidth: 3; radius: 1.5
                                         color: Theme.ladderStrong }
            }
            delegate: Item {
                id: presetRow
                required property var modelData
                required property int index
                width: presetList.width
                height: 28 + (groupBreak ? 9 : 0)
                readonly property bool selected:
                    modelData.key === (page.trev, themeBridge ? themeBridge.activeKey() : "")
                readonly property bool groupBreak: {
                    if (index <= 0) return false;
                    var prev = presetList.model[index - 1];
                    if (modelData.custom === true && prev.custom !== true) return true;
                    return modelData.custom !== true && prev.custom !== true
                           && modelData.dark === false && prev.dark === true;
                }

                Rectangle {
                    visible: presetRow.groupBreak
                    anchors.top: parent.top
                    anchors.topMargin: 4
                    width: parent.width - 16
                    anchors.horizontalCenter: parent.horizontalCenter
                    height: 1
                    color: Theme.border
                }
                Rectangle {
                    anchors.fill: parent
                    anchors.topMargin: presetRow.groupBreak ? 9 : 0
                    color: presetRow.selected ? Qt.rgba(Qt.color(presetRow.modelData.accent).r,
                                                        Qt.color(presetRow.modelData.accent).g,
                                                        Qt.color(presetRow.modelData.accent).b, 0.10)
                         : presetRowHov.hovered ? Theme.hoverWash : "transparent"
                }
                Item {
                    visible: presetRow.selected
                    anchors.left: parent.left
                    anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.verticalCenterOffset: presetRow.groupBreak ? 4 : 0
                    width: 11; height: 11
                    Rectangle { x: 0; y: 5; width: 5; height: 1.6; radius: 1
                                rotation: 45; color: presetRow.modelData.accent }
                    Rectangle { x: 2.6; y: 4.4; width: 8; height: 1.6; radius: 1
                                rotation: -50; color: presetRow.modelData.accent }
                }
                // theme chip (v1.4.1): background fill + centered accent dot answers
                // light-vs-dark at a glance; keyline only on DARK chips (per-chip
                // luminance, not menu mode) so both polarities read on either surface
                Rectangle {
                    objectName: "themeChip_" + presetRow.modelData.key
                    anchors.left: parent.left
                    anchors.leftMargin: 26
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.verticalCenterOffset: presetRow.groupBreak ? 4 : 0
                    width: 14; height: 14; radius: 4
                    color: presetRow.modelData.background
                    border.width: presetRow.modelData.dark === true ? 1 : 0
                    border.color: Qt.rgba(1, 1, 1, 0.20)
                    Rectangle {
                        anchors.centerIn: parent
                        width: 5; height: 5; radius: 2.5
                        color: presetRow.modelData.accent
                    }
                }
                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: 26 + 14 + 8
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.verticalCenterOffset: presetRow.groupBreak ? 4 : 0
                    text: presetRow.modelData.name
                    color: presetRow.selected ? Theme.textPrimary : Theme.textMutedBody
                    font.pixelSize: Theme.fontControl
                }
                HoverHandler { id: presetRowHov }
                TapHandler {
                    onTapped: {
                        themeBridge.setActive(presetRow.modelData.key);
                        presetMenu.close();
                    }
                }
            }
        }
    }

    Popup {
        id: picker
        parent: page
        width: 196
        padding: Theme.spacingMd
        closePolicy: Popup.CloseOnPressOutside
        property string role: ""
        property real h: 0
        property real s: 0
        property real v: 0
        property bool escaped: false
        property bool internalWrite: false

        function openFor(role, anchorItem) {
            picker.role = role;
            page.editingRole = role;
            themeBridge.beginEdit();
            seedFrom(themeBridge.roleValue(role));
            var pt = anchorItem.mapToItem(page, 0, anchorItem.height + 6);
            x = Math.max(0, Math.min(pt.x - width + anchorItem.width, page.width - width));
            y = Math.min(pt.y, page.height - height - 4);
            escaped = false;
            open();
        }
        function seedFrom(hex) {
            var c = Qt.color(hex);
            // preserve hue at the achromatic poles (s or v = 0 collapses hsvHue to -1)
            if (c.hsvSaturation > 0 && c.hsvValue > 0) h = Math.max(0, c.hsvHue);
            s = c.hsvSaturation;
            v = c.hsvValue;
        }
        function commit() {
            internalWrite = true;
            themeBridge.setRoleLive(role, Qt.hsva(h, s, v, 1).toString());
            internalWrite = false;
        }
        onClosed: {
            if (!escaped) themeBridge.endEdit();
            page.editingRole = "";
        }
        focus: true

        background: Rectangle {
            radius: Theme.radiusMd
            color: Theme.surfaceVariant
            border.width: 1
            border.color: Theme.borderStrong
        }
        contentItem: Column {
            spacing: Theme.spacingSm
            // Esc = revert-to-open-values then close (click-away keeps edits). Keys only
            // attaches to Items, so the handler lives HERE, not on the Popup itself.
            focus: true
            Keys.onEscapePressed: {
                picker.escaped = true;
                themeBridge.revertEdit();
                picker.close();
            }

            // SV box: saturation along x, value along y (white->hue, then ->black)
            Item {
                id: svBox
                width: 172; height: 110
                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radiusXs
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: "#FFFFFF" }
                        GradientStop { position: 1; color: Qt.hsva(picker.h, 1, 1, 1) }
                    }
                }
                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radiusXs
                    gradient: Gradient {
                        GradientStop { position: 0; color: "#00000000" }
                        GradientStop { position: 1; color: "#FF000000" }
                    }
                }
                Rectangle {
                    x: picker.s * svBox.width - 6
                    y: (1 - picker.v) * svBox.height - 6
                    width: 12; height: 12; radius: 6
                    color: "transparent"
                    border.width: 2
                    border.color: "#FFFFFF"
                    Rectangle { anchors.fill: parent; anchors.margins: -1; radius: 7
                                color: "transparent"; border.width: 1
                                border.color: "#66000000" }
                }
                MouseArea {
                    anchors.fill: parent
                    function put(mx, my) {
                        picker.s = Math.max(0, Math.min(1, mx / svBox.width));
                        picker.v = Math.max(0, Math.min(1, 1 - my / svBox.height));
                        picker.commit();
                    }
                    onPressed: (m) => put(m.x, m.y)
                    onPositionChanged: (m) => { if (pressed) put(m.x, m.y) }
                }
            }

            Item {
                id: hueStrip
                width: 172; height: 10
                Rectangle {
                    anchors.fill: parent
                    radius: 2
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0;  color: "#FF0000" }
                        GradientStop { position: 0.17; color: "#FFFF00" }
                        GradientStop { position: 0.33; color: "#00FF00" }
                        GradientStop { position: 0.50; color: "#00FFFF" }
                        GradientStop { position: 0.67; color: "#0000FF" }
                        GradientStop { position: 0.83; color: "#FF00FF" }
                        GradientStop { position: 1.0;  color: "#FF0000" }
                    }
                }
                Rectangle {
                    x: picker.h * hueStrip.width - 5
                    y: -1
                    width: 10; height: 12; radius: 5
                    color: "transparent"
                    border.width: 2; border.color: "#FFFFFF"
                }
                MouseArea {
                    anchors.fill: parent
                    function put(mx) {
                        picker.h = Math.max(0, Math.min(0.999, mx / hueStrip.width));
                        picker.commit();
                    }
                    onPressed: (m) => put(m.x)
                    onPositionChanged: (m) => { if (pressed) put(m.x) }
                }
            }

            Row {
                spacing: Theme.spacingSm
                Rectangle {
                    width: 18; height: 18; radius: 9
                    anchors.verticalCenter: parent.verticalCenter
                    color: Qt.hsva(picker.h, picker.s, picker.v, 1)
                    border.width: 1; border.color: Theme.border
                }
                Repeater {
                    model: ["r", "g", "b"]
                    delegate: TextField {
                        id: chan
                        required property string modelData
                        width: 34; height: 22
                        topPadding: 0; bottomPadding: 0; leftPadding: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.textMutedBody
                        font.pixelSize: Theme.fontMicro
                        font.family: Theme.monoFamily
                        background: Rectangle {
                            radius: Theme.radiusXs
                            color: Theme.inputWell
                            border.width: 1
                            border.color: chan.activeFocus ? Theme.borderStrong : Theme.border
                        }
                        readonly property color cc: Qt.hsva(picker.h, picker.s, picker.v, 1)
                        readonly property int bound: Math.round(255 *
                            (modelData === "r" ? cc.r : modelData === "g" ? cc.g : cc.b))
                        onBoundChanged: if (!activeFocus) text = bound
                        Component.onCompleted: text = bound
                        function commitChannel(delta) {
                            var val = parseInt(text);
                            if (isNaN(val)) { text = bound; return; }
                            val = Math.max(0, Math.min(255, val + delta));
                            text = val;
                            var c = Qt.hsva(picker.h, picker.s, picker.v, 1);
                            var r = Math.round(c.r * 255), g = Math.round(c.g * 255),
                                b = Math.round(c.b * 255);
                            if (modelData === "r") r = val;
                            else if (modelData === "g") g = val;
                            else b = val;
                            if (themeBridge.setRoleLive(picker.role,
                                                        "rgb(" + r + "," + g + "," + b + ")"))
                                picker.seedFrom(themeBridge.roleValue(picker.role));
                        }
                        onAccepted: commitChannel(0)
                        Keys.onUpPressed: (e) => commitChannel(e.modifiers & Qt.ShiftModifier ? 10 : 1)
                        Keys.onDownPressed: (e) => commitChannel(e.modifiers & Qt.ShiftModifier ? -10 : -1)
                    }
                }
                Rectangle {
                    visible: themeBridge ? themeBridge.eyedropperAvailable() : false
                    width: 22; height: 22; radius: Theme.radiusXs
                    anchors.verticalCenter: parent.verticalCenter
                    color: dropHov.hovered ? Theme.hoverWash : "transparent"
                    border.width: 1; border.color: Theme.border
                    Rectangle { x: 6; y: 11; width: 8; height: 2.5; radius: 1
                                rotation: -45; color: Theme.textSecondary }
                    Rectangle { x: 12; y: 5; width: 4; height: 4; radius: 1
                                rotation: -45; color: Theme.textSecondary }
                    HoverHandler { id: dropHov }
                    TapHandler {
                        onTapped: {
                            var c = themeBridge.pickScreenColor();
                            if (c) {
                                themeBridge.setRoleLive(picker.role, c);
                                picker.seedFrom(c);
                            }
                        }
                    }
                }
            }
        }
    }
}
