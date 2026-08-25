import QtQuick
import QtQuick.Controls.Basic
import "."

// SettingsRow - the row grammar for the reworked Settings surface.
//
// The baseline grammar wholesale: SRow's 36px floor, 8px gaps and 13px labels are replaced by
// the popup/editor row - 34 for one line, 40 with a caption, 0 row-to-row spacing, a 12.5px
// primary label and a 10.5px tertiary caption on a second line INSIDE the row.
//
// S3 - ONE CONTROL PER ROW, FLUSH RIGHT. The slot is anchored to the row's right edge, which
// is the content column's right edge, so every control on every page lines up on one axis.
// The two Schedule playlist/time rows are the only sanctioned exception and they pass a Row
// into the same single slot rather than opening a second one.
//
// `dim` is the precondition state (sec 3.3): a row whose precondition is off renders at 0.5
// opacity, LABEL AND CONTROL TOGETHER, so it reads as one unavailable fact rather than a
// disabled widget beside a live label.
Item {
    id: srow

    property string label: ""
    property string caption: ""
    property bool dim: false
    // real-typed: font.pixelSize is an int property, so a 12.5 literal is a
    // type error while a real-typed binding converts (as Theme.fontMicro does)
    readonly property real fontRow: 12.5
    default property alias slot: holder.data

    width: parent ? parent.width : 0
    // 34 for one line, 40 with a caption - fixed, not content-derived. The label column's
    // own implicit height plus breathing room lands at 42 for a two-line row, which is the
    // kind of drift that makes a 16-row page 30px taller than the drawing; the caption is a
    // single elided line, so the drawn number is the right number.
    implicitHeight: Math.max(caption !== "" ? 40 : 34, holder.childrenRect.height)
    opacity: dim ? 0.5 : 1.0

    Column {
        id: labelCol
        anchors.left: parent.left
        anchors.right: holder.left
        anchors.rightMargin: Theme.spacingSm
        anchors.verticalCenter: parent.verticalCenter
        spacing: 1

        Label {
            width: parent.width
            text: srow.label
            color: Theme.textPrimary
            font.pixelSize: srow.fontRow
            elide: Text.ElideRight
        }
        Label {
            width: parent.width
            // no `height: visible ? implicitHeight : 0` on an eliding Label - that binding is
            // a height/implicitHeight loop, and the Column already drops invisible children
            visible: srow.caption !== ""
            text: srow.caption
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMicro
            elide: Text.ElideRight
        }
    }

    Item {
        id: holder
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: childrenRect.width
        height: childrenRect.height
    }
}
