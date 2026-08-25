import QtQuick
import QtQuick.Controls.Basic
import "."

Item {
    id: row

    property string label: ""
    property string desc: ""
    default property alias control: slot.data

    // Respect the container's padding. Taking the raw parent.width made the row overflow a
    // padded container, so its right-slotted control sat flush against the container's edge -
    // visible on the schedule modal's enable toggle. Guarded with || 0 because most callers
    // (the settings surfaces) sit in UNPADDED columns, where these properties are undefined
    // and the row must keep its full width.
    width: parent ? parent.width - (parent.leftPadding || 0) - (parent.rightPadding || 0) : 640

    // TWO-LINE STACKING. At compact, a row stacks - label on top,
    // control full-width beneath - when its value is a MONO PATH (the caller declares it:
    // a path competing with its own label is unreadable at any width worth stacking for)
    // OR when the control eats more than 40% of the width. Below both thresholds the row
    // stays inline, because stacking a row whose control is a 30px switch wastes a line.
    property bool monoValue: false
    readonly property bool stacked: Theme.compact && (monoValue || slot.width > row.width * 0.4)

    implicitHeight: stacked
        ? left.implicitHeight + Theme.spacingXs + slot.height + Theme.spacingSm
        : Math.max(36, left.implicitHeight + Theme.spacingSm,
                   slot.childrenRect.height + Theme.spacingSm)

    Column {
        id: left
        anchors.verticalCenter: row.stacked ? undefined : parent.verticalCenter
        anchors.top: row.stacked ? parent.top : undefined
        anchors.left: parent.left
        spacing: 2
        width: row.stacked ? parent.width
                           : Math.max(0, parent.width - slot.width - Theme.spacingLg)

        Label {
            text: row.label
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody13
            width: parent.width
            elide: Text.ElideRight
        }
        Label {
            visible: row.desc !== ""
            text: row.desc
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMeta
            wrapMode: Text.WordWrap
            width: parent.width
        }
    }

    Item {
        id: slot
        anchors.right: row.stacked ? undefined : parent.right
        anchors.left: row.stacked ? parent.left : undefined
        anchors.verticalCenter: row.stacked ? undefined : parent.verticalCenter
        anchors.top: row.stacked ? left.bottom : undefined
        anchors.topMargin: row.stacked ? Theme.spacingXs : 0
        width: childrenRect.width
        height: childrenRect.height
    }
}
