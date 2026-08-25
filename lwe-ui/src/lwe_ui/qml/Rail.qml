import QtQuick
import QtQuick.Controls.Basic
import "."

Rectangle {
    id: rail

    property string currentScope: "all"
    // Which rail item actually shows the wash/bar right now (see doc comment above).
    property string activeItem: currentScope
    property int reviewCount: 0
    readonly property int markGap: 14

    signal scopeSelected(string scope)
    signal developerRequested()
    signal settingsRequested()

    width: 64
    color: Theme.base

    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: Theme.border
    }

    component RailItem: Item {
        id: item
        property string label: ""
        property bool active: false
        property int dotCount: 0
        // the item's drawn glyph is declared at the call site into this slot (so it can bind
        // its color to the item's tint from that scope); it is laid above the label. The slot
        // holds exactly one icon, which drives the glyph box size.
        default property alias glyph: iconSlot.data
        readonly property color tint: active ? Theme.textPrimary : Theme.textSecondary
        signal clicked()

        width: 56
        height: 44

        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusMd
            color: item.active ? Theme.activeWash
                 : hover.hovered ? Theme.hoverWash : "transparent"
        }

        Column {
            anchors.centerIn: parent
            spacing: Theme.spacingXs
            // glyph host: the slotted icon sizes iconSlot and stays centered. The badge is
            // NOT parented here anymore - it anchors to the BUTTON's top-right corner (below)
            // so it never rides over the centered glyph.
            Item {
                id: glyphBox
                anchors.horizontalCenter: parent.horizontalCenter
                width: iconSlot.width
                height: iconSlot.height
                Item {
                    id: iconSlot
                    width: childrenRect.width
                    height: childrenRect.height
                }
            }
            Label {
                anchors.horizontalCenter: parent.horizontalCenter
                text: item.label
                color: item.tint
                font.pixelSize: Theme.fontMeta
            }
        }

        Rectangle {
            visible: item.dotCount > 0
            height: 14; radius: 7
            width: Math.max(14, badgeLabel.implicitWidth + 6)
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.rightMargin: 6
            anchors.topMargin: 6
            color: Theme.accent
            Label {
                id: badgeLabel
                anchors.centerIn: parent
                text: item.dotCount > 9 ? "9+" : String(item.dotCount)
                color: Theme.onAccent
                font.pixelSize: 10
                font.weight: Theme.weightMedium
            }
        }

        HoverHandler { id: hover }
        TapHandler { onTapped: item.clicked() }
    }

    Column {
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingMd
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: Theme.spacingXs

        Rectangle {
            width: 17; height: 17; radius: 5
            color: Theme.accent
            anchors.horizontalCenter: parent.horizontalCenter
        }
        // G6: the mark sits 14px above the first item. The Column's spacing adds a
        // 4px gap on BOTH sides of this spacer (mark->spacer and spacer->item0), so the spacer
        // height is markGap(14) - 2*spacingXs(4) = 6, making the total 4 + 6 + 4 = 14.
        Item { width: 1; height: rail.markGap - Theme.spacingXs * 2 }

        RailItem {
            id: allItem
            label: "All"
            active: rail.activeItem === "all"
            onClicked: { rail.currentScope = "all"; rail.scopeSelected("all") }
            IconGridDots { size: 12; color: allItem.tint }
        }
        RailItem {
            id: favItem
            label: "Favorites"
            active: rail.activeItem === "favorites"
            onClicked: { rail.currentScope = "favorites"; rail.scopeSelected("favorites") }
            IconStar { size: 15; color: favItem.tint }
        }
        RailItem {
            id: workshopItem
            label: "Workshop"
            active: rail.activeItem === "workshop"
            dotCount: rail.reviewCount
            onClicked: rail.scopeSelected("workshop")
            Item {
                width: 13; height: 13
                Rectangle {
                    anchors.centerIn: parent
                    width: 13; height: 2; radius: 1
                    color: workshopItem.tint
                }
                Rectangle {
                    anchors.centerIn: parent
                    width: 2; height: 13; radius: 1
                    color: workshopItem.tint
                }
            }
        }
    }

    Column {
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Theme.spacingMd
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: Theme.spacingXs

        RailItem {
            id: devItem
            label: "Developer"
            active: rail.activeItem === "developer"
            onClicked: rail.developerRequested()
            IconCode { size: 16; color: devItem.tint }
        }
        RailItem {
            id: settingsItem
            label: "Settings"
            active: rail.activeItem === "settings"
            onClicked: rail.settingsRequested()
            IconGear { size: 16; color: settingsItem.tint }
        }
    }

    // active indicator bar at the rail's left edge, aligned to whichever item is active
    // (scope item OR a mounted takeover - see activeItem doc comment above).
    Rectangle {
        width: 2; height: 18
        color: Theme.accent
        x: 0
        visible: topIndex >= 0 || bottomIndex >= 0
        property int topIndex: rail.activeItem === "all" ? 0
                              : rail.activeItem === "favorites" ? 1
                              : rail.activeItem === "workshop" ? 2 : -1
        property int bottomIndex: rail.activeItem === "developer" ? 0
                                 : rail.activeItem === "settings" ? 1 : -1
        y: {
            if (topIndex >= 0) {
                // top of item0 = topMargin(12) + mark(17) + column pitch(4) + mark spacer(6)
                // + column pitch(4) = 43. The spacer is markGap(14) - 2*spacingXs(4) = 6, so
                // the mark-to-item0 gap (4 + spacer + 4) is the drawn 14.
                var spacer = rail.markGap - Theme.spacingXs * 2;
                var base = Theme.spacingMd + 17 + Theme.spacingXs
                         + spacer + Theme.spacingXs;
                return base + topIndex * (44 + Theme.spacingXs) + (44 - 18) / 2;
            }
            var rowsFromBottom = 1 - bottomIndex;
            var fromBottom = Theme.spacingMd
                           + rowsFromBottom * (44 + Theme.spacingXs)
                           + 44 - (44 - 18) / 2;
            return rail.height - fromBottom;
        }
        Behavior on y { NumberAnimation { duration: 150 } }
    }
}
