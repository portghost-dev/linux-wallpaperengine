import QtQuick
import QtQuick.Controls.Basic
import "."

// SettingsField - the text / numeric field.
//
// h24; width is the consumer's (schedule time field 60, `Check every` 78). Commit is on
// ENTER OR BLUR, and ESCAPE REVERTS TO STORE TRUTH AND COMMITS NOTHING (sec 6.1) - which is
// why `storeText` exists: the field never keeps a draft of its own, it re-reads the store.
Rectangle {
    id: field

    property string storeText: ""
    property bool failed: false
    property string suffix: ""

    signal entered(string text)

    // for consumers that drive the field imperatively (the exceptions editor's add row)
    function currentText() { return input.text }
    function clear() { input.text = ""; }

    height: 24
    width: 78
    radius: Theme.radiusSm
    color: Theme.surface
    border.width: failed ? 1.5 : 1
    border.color: failed ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.9)
                : (input.activeFocus ? Theme.accent : Theme.border)

    onStoreTextChanged: if (!input.activeFocus) input.text = storeText

    Row {
        anchors.fill: parent
        anchors.leftMargin: 8
        anchors.rightMargin: 8
        spacing: 3

        TextInput {
            id: input
            width: parent.width - (suffixLabel.visible ? suffixLabel.width + 3 : 0)
            height: parent.height
            verticalAlignment: Text.AlignVCenter
            color: Theme.textPrimary
            font.pixelSize: Theme.fontMeta
            selectByMouse: true
            text: field.storeText
            Keys.onReturnPressed: field.entered(input.text)
            Keys.onEnterPressed: field.entered(input.text)
            Keys.onEscapePressed: { input.text = field.storeText; input.focus = false; }
            onActiveFocusChanged: if (!activeFocus) field.entered(input.text)
        }
        Label {
            id: suffixLabel
            anchors.verticalCenter: parent.verticalCenter
            visible: field.suffix !== ""
            text: field.suffix
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMeta
        }
    }
}
