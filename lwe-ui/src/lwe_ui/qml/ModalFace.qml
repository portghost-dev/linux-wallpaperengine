import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import "."

// The shared modal face (component library 19a.3). ONE chassis for the bench cards AND the
// trash confirm. Five stacked slots, top-down, CONTENT-HEIGHT (grows to its content, never a
// fixed max - the fixed face was what caused the built voids):
//
//   [banner]   optional thumbnail with scrim + fade-to-surface (bench cards); type pill on it
//   header     optional eyebrow (+ optional status dot) then the title; X top-right always
//   body       one descriptive paragraph
//   middle     optional well OR field (via middleContent) - never a step-dot row
//   primary    full-width, semantic fill
//   secondary  full-width ghost; danger-ghost when destructive
//   footer     optional hairline + one line
//
// Banner STANDING LAW: the thumb always carries a dark scrim + a fade to the card surface, so
// hostile baked-in art (Red Space's bars) stays muted on every theme - never a raw thumb.
Item {
    id: face

    // banner (bench cards); empty source = header-title mode (trash)
    property url bannerSource: ""
    property string typePill: ""
    property bool bannerGrim: false
    property bool hasDot: false
    property color dotColor: Theme.textTertiary
    property string eyebrow: ""
    property string title: ""
    property string body: ""
    property string primaryText: ""
    property color primaryColor: Theme.accent
    property string secondaryText: ""
    property bool secondaryDanger: false
    property string footerText: ""
    property alias middleContent: middleSlot.data

    readonly property bool hasBanner: bannerSource.toString() !== ""
    readonly property int pad: Theme.spacingLg

    signal primaryClicked()
    signal secondaryClicked()
    signal closeClicked()

    implicitWidth: 380
    implicitHeight: col.implicitHeight

    Column {
        id: col
        width: face.width

        Item {
            id: banner
            width: parent.width
            height: 128
            visible: face.hasBanner
            clip: true

            // art stack, masked to the card's rounded TOP corners (v2.3.7 corner-bleed law:
            // 1px inset, mask radius = card radius - 1, so the AA seam hides under the card
            // border and no image pixel ever crosses the radius or the border stroke)
            Item {
                id: bannerArt
                anchors.fill: parent
                anchors.margins: 1
                layer.enabled: true
                layer.effect: MultiEffect {
                    maskEnabled: true
                    // an explicit source with hideSource: a bare Item reference only
                    // provides a texture while it renders, and an invisible mask
                    // renders nothing - which masks the whole banner away
                    maskSource: ShaderEffectSource { sourceItem: bannerMask; hideSource: true }
                    maskThresholdMin: 0.5
                    maskSpreadAtMin: 1.0
                }

                Image {
                    anchors.fill: parent
                    source: face.bannerSource
                    fillMode: Image.PreserveAspectCrop
                    sourceSize.width: Theme.previewCap
                    asynchronous: true
                    cache: true
                }
                // dark scrim: mute hostile art on every theme; deepens on a crash verdict (21e)
                Rectangle {
                    anchors.fill: parent
                    color: face.bannerGrim ? "#8C000000" : "#59000000"
                    Behavior on color { ColorAnimation { duration: Motion.wake } }
                }
                // fade to the card surface at the bottom edge, so the header below reads
                Rectangle {
                    anchors.fill: parent
                    gradient: Gradient {
                        GradientStop { position: 0.35; color: "transparent" }
                        GradientStop { position: 1.0; color: Theme.surface }
                    }
                }
            }
            Rectangle {
                id: bannerMask
                anchors.fill: bannerArt
                color: "white"
                topLeftRadius: Theme.radiusLg - 1
                topRightRadius: Theme.radiusLg - 1
            }
            Chip {
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.margins: Theme.spacingMd
                kind: "type"
                text: face.typePill
                shell: Theme.scrimPlate
                visible: face.typePill !== ""
            }
        }

        Column {
            width: parent.width
            topPadding: face.pad
            leftPadding: face.pad
            rightPadding: face.pad
            bottomPadding: 14
            spacing: Theme.spacingMd

            // header: eyebrow (with optional dot) then title, OR dot+title when no eyebrow
            Column {
                width: parent.width - face.pad * 2
                spacing: Theme.spacingXs

                Row {
                    spacing: Theme.spacingSm
                    visible: face.eyebrow !== "" || face.title !== ""
                    Rectangle {
                        width: 6; height: 6; radius: 3
                        anchors.verticalCenter: parent.verticalCenter
                        visible: face.hasDot
                        color: face.dotColor
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        // cap to the face width (minus the dot and the close X) - a long
                        // wallpaper title in a header-title modal must elide, not overflow
                        width: parent.parent.width - (face.hasDot ? 6 + Theme.spacingSm : 0)
                               - Theme.spacingLg
                        text: face.eyebrow !== "" ? face.eyebrow : face.title
                        elide: Text.ElideRight
                        color: Theme.textSecondary
                        font.pixelSize: face.eyebrow !== "" ? Theme.fontMeta : Theme.fontNav
                        font.weight: face.eyebrow !== "" ? Theme.weightRegular : Theme.weightMedium
                    }
                }
                Label {
                    width: parent.width
                    visible: face.eyebrow !== "" && face.title !== ""
                    text: face.title
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontNav
                    font.weight: Theme.weightMedium
                    elide: Text.ElideRight
                }
            }

            Label {
                width: parent.width - face.pad * 2
                visible: face.body !== ""
                text: face.body
                color: Theme.textSecondary
                font.pixelSize: Theme.fontBody13
                wrapMode: Text.WordWrap
            }

            // middle slot: caller drops a Well or a ThemedField here
            Item {
                id: middleSlot
                width: parent.width - face.pad * 2
                height: childrenRect.height
                // collapse (and let the Column skip its spacing) when the slot content is
                // effectively empty - e.g. a note field that only shows on the verdict cards
                visible: childrenRect.height > 0
            }

            Button {
                id: primaryBtn
                width: parent.width - face.pad * 2
                height: 40
                visible: face.primaryText !== ""
                onClicked: face.primaryClicked()
                contentItem: Label {
                    text: face.primaryText
                    color: Theme.onAccent
                    font.pixelSize: Theme.fontControl
                    font.weight: Theme.weightMedium
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: Theme.radiusSm
                    color: primaryBtn.hovered ? Qt.lighter(face.primaryColor, 1.1) : face.primaryColor
                }
            }

            Button {
                id: secondaryBtn
                width: parent.width - face.pad * 2
                height: 36
                visible: face.secondaryText !== ""
                onClicked: face.secondaryClicked()
                contentItem: Label {
                    text: face.secondaryText
                    color: face.secondaryDanger ? Theme.danger : Theme.textSecondary
                    font.pixelSize: Theme.fontControl
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: Theme.radiusSm
                    color: secondaryBtn.hovered ? Theme.hoverWash : "transparent"
                    border.width: 1
                    border.color: face.secondaryDanger ? Theme.danger : Theme.border
                }
            }

            Column {
                width: parent.width - face.pad * 2
                visible: face.footerText !== ""
                spacing: Theme.spacingSm
                topPadding: Theme.spacingXs
                Rectangle { width: parent.width; height: 1; color: Theme.border }
                Label {
                    text: face.footerText
                    color: Theme.textTertiary
                    font.pixelSize: Theme.fontMeta
                }
            }
        }
    }

    Rectangle {
        id: closeBtn
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.margins: Theme.spacingMd
        width: 28; height: 28; radius: Theme.radiusSm
        color: closeHover.hovered ? Theme.scrimPlate : (face.hasBanner ? "#59000000" : "transparent")
        IconX {
            anchors.centerIn: parent
            size: 12
            // imagery law (v2.3.7): whenever the X sits on a dark plate (banner, or the hover
            // scrim plate) the glyph is fixed light on all 14 themes; themed only on bare card
            color: (face.hasBanner || closeHover.hovered) ? Theme.imageryText : Theme.textPrimary
        }
        HoverHandler { id: closeHover }
        TapHandler { onTapped: face.closeClicked() }
    }
}
