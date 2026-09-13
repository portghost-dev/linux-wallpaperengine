import QtQuick
import QtQuick.Controls.Basic
import "."

// The interval popover: one row, the number and a fixed unit of minutes. No title, no
// buttons, no mode controls (the mode lives on the transport); every change commits live.
// Anchored by its owner above the stopwatch cell, right-aligned to the pill.
Popup {
    id: pop

    // the active playlist as the strip holds it: {mode, interval, ...}
    property var activePl: ({mode: "shuffle", interval: 900, unit: "min"})
    readonly property bool isStatic: (activePl.mode || "shuffle") === "static"

    // the interval as the field DISPLAYS it, whole minutes rounded up so a stored value under a
    // minute never reads as more than it is
    function shownInterval() {
        return Math.max(1, Math.ceil((activePl.interval || 900) / 60));
    }
    // the one commit path: a typed value lands, an empty or zero field re-reads the stored one.
    // The guard compares seconds, so a stored value that is not a whole minute can be replaced
    function commitField() {
        var v = parseInt(intervalField.text);
        if (isNaN(v) || v < 1 || v > 1440) { intervalField.text = String(shownInterval()); return; }
        if (v * 60 === (activePl.interval | 0))
            return;
        backend.setPlaylistInterval(v, "min");
    }

    height: 40
    topPadding: 0
    bottomPadding: 0
    leftPadding: 12
    rightPadding: 12
    property bool justClosed: false
    onClosed: { justClosed = true; guard.restart() }
    Timer { id: guard; interval: 150; onTriggered: pop.justClosed = false }

    background: Rectangle {
        color: Theme.surface
        radius: 8
        border.width: 1
        border.color: Theme.hairlineStrong
        // the padding ring around the content: holds the press so it cannot reach a tile beneath
        TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; grabPermissions: PointerHandler.TakeOverForbidden }
    }

    contentItem: Row {
        objectName: "everyRow"
        spacing: 12
        // asleep in static: the timer does not run, but the value stays editable, so a
        // change made while static is what the next start uses
        opacity: pop.isStatic ? 0.55 : 1
        // holds the exclusive grab from the press to the release, even once the pointer has
        // left the popover, so a drag that starts here can never lift a tile beneath; it takes
        // nothing from the controls inside (TakeOverForbidden)
        TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; grabPermissions: PointerHandler.TakeOverForbidden }

        Label {
            anchors.verticalCenter: parent.verticalCenter
            text: "Interval"
            color: Theme.textPrimary
            // the spec's 12.5 and 11.5 px land on the theme's 12 and 11: pixel sizes are whole
            font.pixelSize: Theme.fontControl
        }
        TextField {
            id: intervalField
            objectName: "intervalField"
            anchors.verticalCenter: parent.verticalCenter
            width: 48
            height: 24
            text: String(pop.shownInterval())
            color: Theme.textPrimary
            font.pixelSize: Theme.fontControl
            horizontalAlignment: Text.AlignRight
            verticalAlignment: TextInput.AlignVCenter
            topPadding: 0
            bottomPadding: 0
            // digits only, 1..1440 minutes. The validator only caps the top so a typed 0 or an
            // emptied field still reaches the handler, which rejects and re-reads
            validator: IntValidator { bottom: 0; top: 1440 }
            background: Rectangle {
                color: Theme.inputWell
                radius: 6
                border.width: 1
                border.color: intervalField.activeFocus ? Theme.borderStrong : Qt.rgba(1, 1, 1, 0.12)
            }
            // Enter and focus loss both commit; an empty field never reaches
            // editingFinished (not acceptable to the validator), so they are handled here
            Keys.onReturnPressed: { pop.commitField(); focus = false }
            Keys.onEnterPressed: { pop.commitField(); focus = false }
            onActiveFocusChanged: if (!activeFocus) pop.commitField()
            onEditingFinished: pop.commitField()
            // a drag that starts in the field is taken from it here and held to the release, so
            // it can never reach a tile beneath. The price is drag-selection by mouse; clicks,
            // caret placement, double-click and keyboard selection stay
            DragHandler { target: null; grabPermissions: PointerHandler.CanTakeOverFromItems }
        }
        Label {
            anchors.verticalCenter: parent.verticalCenter
            text: "min"
            color: Theme.textTertiary
            font.pixelSize: Theme.fontMeta
        }
    }
}
