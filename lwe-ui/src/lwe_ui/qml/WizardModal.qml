import QtQuick
import QtQuick.Controls.Basic
import "."

Popup {
    id: wizardModal
    objectName: "wizardModal"

    property string ph: ""
    property string blockedMsg: ""
    readonly property bool isBefore: ph === "p1" || ph === "p2"
    readonly property bool isVerdict: ph === "pass" || ph === "fixable" || ph === "fail"
    readonly property bool isCrash: ph === "fixable" || ph === "fail"
    // compression phase (workshop-bench R1): c0 = inspecting, c1 = state-aware offer,
    // c2 = encoding with live progress. Scene wallpapers only; the bridge routes
    // video/web straight to p1.
    readonly property bool isComp: ph === "c0" || ph === "c1" || ph === "c2"
    property int compRev: 0
    readonly property var compFacts: (compRev, wizardBridge.compFacts())
    readonly property bool compHasWork: (compFacts.todo || 0) > 0 && compFacts.shim === true

    Connections {
        target: wizardBridge
        function onPhaseChanged() {
            wizardModal.blockedMsg = "";
            wizardModal.ph = wizardBridge.phase();
            if (wizardModal.ph === "" || wizardModal.ph === "p3") {
                noteField.text = "";
                wizardModal.close();
            } else if (!wizardModal.visible) {
                noteField.text = "";
                wizardModal.open();
            }
        }
        function onCompChanged() { wizardModal.compRev++ }
        function onBenchBlocked(msg) { wizardModal.blockedMsg = msg; blockTimer.restart(); }
    }
    Timer { id: blockTimer; interval: 2500; onTriggered: wizardModal.blockedMsg = "" }

    anchors.centerIn: parent
    // sec 3 modal guard: every face is already <=620 and none was width-responsive, so
    // below a 660 window they clamp to width-24 (12px side margins) instead of
    // overhanging. Theme.usableWidth excludes the rail; modals center on the WINDOW,
    // so the rail's 64px is added back before comparing.
    width: Math.min(380, Theme.usableWidth + 64 - 24)
    modal: true
    focus: true
    padding: 0
    closePolicy: Popup.NoAutoClose
    background: Rectangle {
        radius: Theme.radiusLg
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderStrong
        clip: true
    }
    Overlay.modal: Rectangle { color: Theme.scrimHover }

    contentItem: ModalFace {
        id: face
        width: parent.width

        bannerSource: (wizardModal.ph !== "" && wizardModal.ph !== "p3")
                      ? backend.thumbUrl(wizardBridge.wid()) : ""
        typePill: wizardModal.ph !== "" ? wizardBridge.wpType() : ""
        title: wizardBridge.wpTitle()

        eyebrow: wizardModal.isComp ? "Compression"
               : wizardModal.isBefore ? "Bench test"
               : wizardModal.ph === "pass" ? "Ran clean"
               : wizardModal.isCrash ? "Wouldn't run"
               : ""
        bannerGrim: wizardModal.isVerdict && wizardModal.ph !== "pass"
                body: wizardModal.ph === "c0"
                ? "Inspecting textures."
             : wizardModal.ph === "c1"
                ? (wizardModal.compHasWork
                    ? "For RAM/VRAM resource efficiency, we recommend processing textures to compress them. This may take a minute."
                      + "\n\n" + (wizardModal.compFacts.todo || 0) + " of "
                      + (wizardModal.compFacts.total || 0) + " textures, about "
                      + (wizardModal.compFacts.raw_mb || 0) + " MB to "
                      + (wizardModal.compFacts.bc_mb || 0) + " MB."
                   : wizardModal.compFacts.shim !== true && (wizardModal.compFacts.todo || 0) > 0
                    ? "The compression tool is not available on this system."
                   : (wizardModal.compFacts.eligible || 0) === 0
                    ? "All textures ship pre-compressed. Nothing to process."
                    : "Textures are already processed. Nothing to do.")
             : wizardModal.ph === "c2"
                ? "Compressing textures. " + wizardBridge.compDone() + " of "
                  + ((wizardModal.compRev, wizardBridge.compTotal())) + " done."
             : wizardModal.isBefore
                ? "Opens the scene in a window for about 15 seconds so you can watch it run. The bench only checks that it launches. Getting it to work is hands-on."
             : wizardModal.ph === "pass"
                ? "It launched and stayed responsive. Your call: add it to the library, or trash it."
             : wizardModal.isCrash
                ? "It crashed at the bench. Some scenes come right with a fix in the editor. Keep it and dig in, or trash it."
             : ""

        primaryText: wizardModal.blockedMsg !== "" ? wizardModal.blockedMsg
                   : wizardModal.ph === "c0" || wizardModal.ph === "c2" ? ""
                   : wizardModal.ph === "c1"
                     ? (wizardModal.compHasWork ? "Start Compression" : "Continue")
                   : wizardModal.isBefore ? "Start bench test"
                   : wizardModal.ph === "pass" ? "Add to library"
                   : wizardModal.ph === "fixable" ? "Apply fixes and retry"
                   : wizardModal.ph === "fail" ? "Trash it"
                   : ""
        primaryColor: wizardModal.blockedMsg !== "" ? Theme.danger
                    : wizardModal.ph === "fail" ? Theme.danger
                    : Theme.accent
        secondaryText: wizardModal.ph === "c1" && wizardModal.compHasWork
                       ? "Skip, do not compress"
                     : wizardModal.isBefore ? "Skip, import untested"
                     : wizardModal.ph === "pass" ? "Deny, trash it"
                     : wizardModal.isCrash ? "Keep and fix later"
                     : ""
        secondaryDanger: wizardModal.ph === "pass"

        onPrimaryClicked: {
            if (wizardModal.blockedMsg !== "") return;
            if (wizardModal.ph === "c1") {
                if (wizardModal.compHasWork) wizardBridge.startCompression();
                else wizardBridge.skipCompression();
            } else if (wizardModal.isBefore) {
                if (wizardModal.ph === "p1") wizardBridge.runWizard();
                if (wizardBridge.phase() === "p2") wizardBridge.proceedToBench();
            } else if (wizardModal.ph === "pass") {
                wizardBridge.approve(noteField.text);
            } else if (wizardModal.ph === "fixable") {
                wizardBridge.applyFixesAndRetry();
            } else if (wizardModal.ph === "fail") {
                wizardBridge.deny(noteField.text);
            }
        }
        onSecondaryClicked: {
            if (wizardModal.ph === "c1") wizardBridge.skipCompression();
            else if (wizardModal.isBefore) wizardBridge.importUntested(noteField.text);
            else if (wizardModal.ph === "pass") wizardBridge.deny(noteField.text);
            else if (wizardModal.isCrash) wizardBridge.cancel(noteField.text);
        }
        onCloseClicked: wizardBridge.cancel(noteField.text)

        middleContent: ThemedField {
            id: noteField
            anchors.left: parent.left
            anchors.right: parent.right
            visible: wizardModal.isVerdict
            height: wizardModal.isVerdict ? implicitHeight : 0
            placeholderText: "Add a note (optional)"
        }
    }
}
