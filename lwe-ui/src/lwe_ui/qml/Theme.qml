pragma Singleton
import QtQuick
import LweUi.Theme 1.0

// Theme singleton, the only place colors and metrics are defined.
//
// Color tokens come from the Python-registered ThemeTokens singleton (app.py builds it
// from storage.theme_cfg.resolve_tokens). Reading them through that singleton, rather
// than a root-context property, guarantees they are reachable from inside this QML-file
// singleton at runtime. Each token passes its "True Black" literal as the fallback, so if
// the Python singleton is unavailable (e.g. a bare lint run) the file still renders a
// valid theme. The linter cannot resolve the Python URI, so it emits an import-resolution
// warning for the import and the ThemeTokens references; that is expected and harmless.
//
// Every color binding references `rev` (comma expression), and rev bumps when the Python
// side emits ThemeTokens.changed, so a live re-theme (accent change, matugen follow)
// repaints the whole app without a restart.
QtObject {
    id: theme

    property int rev: 0
    property var _tokenWatch: Connections {
        target: ThemeTokens
        function onChanged() { theme.rev++ }
    }

    // decode cap for every preview Image's sourceSize.width - ONE shared value
    // (the pixmap cache keys on (file, sourceSize); a second cap duplicates entries)
    readonly property int previewCap: ThemeTokens.previewCap()

    // --- color tokens (v1.0 section 1; wash/scrim values are #AARRGGBB alpha colors) ----
    readonly property color base:           (rev, ThemeTokens.color("base", "#000000"))
    readonly property color surface:        (rev, ThemeTokens.color("surface", "#141414"))
    readonly property color surfaceVariant: (rev, ThemeTokens.color("surfaceVariant", "#1E1E1E"))
    readonly property color inputWell:      (rev, ThemeTokens.color("inputWell", "#0D0D0D"))
    readonly property color border:         (rev, ThemeTokens.color("border", "#1FFFFFFF"))
    readonly property color borderStrong:   (rev, ThemeTokens.color("borderStrong", "#33FFFFFF"))

    readonly property color hairlineFaint:  isLight ? Qt.rgba(0, 0, 0, 0.06) : Qt.rgba(1, 1, 1, 0.06)
    readonly property color hairline:       isLight ? Qt.rgba(0, 0, 0, 0.12) : Qt.rgba(1, 1, 1, 0.12)
    readonly property color hairlineStrong: isLight ? Qt.rgba(0, 0, 0, 0.20) : Qt.rgba(1, 1, 1, 0.20)
    readonly property color hoverWash:      (rev, ThemeTokens.color("hoverWash", "#0FFFFFFF"))
    readonly property color activeWash:     (rev, ThemeTokens.color("activeWash", "#14FFFFFF"))
    // imagery law (v2.3.7): plates and text sitting on ARTWORK are never themed - fixed dark
    // plate + light text on all 14 themes. Only surfaces below/around the imagery theme.
    readonly property color scrimPlate:     "#8C000000"
    readonly property color imageryText:    "#F2F2F2"
    readonly property color scrimHover:     (rev, ThemeTokens.color("scrimHover", "#59000000"))
    readonly property color textPrimary:    (rev, ThemeTokens.color("textPrimary", "#F2F2F2"))
    readonly property color textSecondary:  (rev, ThemeTokens.color("textSecondary", "#A0A0A0"))
    readonly property color textTertiary:   (rev, ThemeTokens.color("textTertiary", "#6E6E6E"))
    readonly property color textMutedBody:  (rev, ThemeTokens.color("textMutedBody", "#D4D4D4"))
    readonly property color accent:         (rev, ThemeTokens.color("accent", "#7F77DD"))
    readonly property color onAccent:       (rev, ThemeTokens.color("onAccent", "#0D0D12"))
    readonly property color selectionWash:  (rev, ThemeTokens.color("selectionWash", "#147F77DD"))
    readonly property color segmentWash:     (rev, ThemeTokens.color("segmentWash", "#1AFFFFFF"))
    // card hover-checkbox border, fixed white 0.35 (rgba(255,255,255,0.35))
    readonly property color checkHoverBorder: (rev, ThemeTokens.color("checkHoverBorder", "#59FFFFFF"))
    readonly property color danger:         (rev, ThemeTokens.color("danger", "#E24B4A"))
    readonly property color dangerWash:     (rev, ThemeTokens.color("dangerWash", "#2EE24B4A"))
    readonly property color warning:        (rev, ThemeTokens.color("warning", "#EF9F27"))
    readonly property color warningWash:    (rev, ThemeTokens.color("warningWash", "#24EF9F27"))
    readonly property color success:        (rev, ThemeTokens.color("success", "#5DCAA5"))

    // v1.4 light pass: the neutral alpha ladder (flips white/black with the theme) and
    // the toggle off-state tokens (dual-value law 4; the on-knob is white in both modes)
    readonly property color ladderSoft:     (rev, ThemeTokens.color("ladderSoft", "#0FFFFFFF"))
    readonly property color ladderMid:      (rev, ThemeTokens.color("ladderMid", "#1FFFFFFF"))
    readonly property color ladderStrong:   (rev, ThemeTokens.color("ladderStrong", "#33FFFFFF"))
    readonly property color toggleOffTrack: (rev, ThemeTokens.color("toggleOffTrack", "#1E1E1E"))
    readonly property color toggleOffBorder:(rev, ThemeTokens.color("toggleOffBorder", "#33FFFFFF"))
    readonly property color toggleOffKnob:  (rev, ThemeTokens.color("toggleOffKnob", "#A0A0A0"))
    readonly property color toggleKnobOn:   (rev, ThemeTokens.color("toggleKnobOn", "#FFFFFF"))
    // type-badge text: #D4D4D4 tinted 18% toward the theme accent (plate = legibility,
    // text = identity; Design amendment to the never-themed law)
    readonly property color badgeText:      (rev, ThemeTokens.color("badgeText", "#D4D4D4"))

    readonly property bool isLight: (base.r * 0.299 + base.g * 0.587 + base.b * 0.114) > 0.5

    readonly property int spacingXs: 4
    readonly property int spacingSm: 8
    readonly property int spacingMd: 12
    readonly property int spacingLg: 16
    readonly property int spacingXl: 24

    readonly property int radiusXs: 4
    readonly property int radiusSm: 6
    readonly property int radiusMd: 8
    readonly property int radiusLg: 12

    readonly property int fontTitle: 18
    readonly property int fontNav: 16
    readonly property int fontDeckName: 15
    readonly property int fontBody13: 13
    readonly property int fontControl: 12
    readonly property int fontMeta: 11
    readonly property real fontMicro: 10.5
    readonly property string monoFamily: "monospace"

    readonly property int fontH1: 22
    readonly property int fontH2: 18
    readonly property int fontH3: 16
    readonly property int fontBody: 14
    readonly property int fontLabel: 13
    readonly property int fontCaption: 11

    readonly property int weightRegular: Font.Normal
    readonly property int weightMedium: Font.Medium

    // --- responsive band --------------------------------------------
    // USABLE width = window width minus the 64px rail, because the rail never sheds and
    // every surface anchors to its right edge. Main.qml pushes this on every resize; these
    // are WRITABLE (unlike every token above) for the same reason Motion.reducedMotion is -
    // a singleton cannot see the window, so the value has to come the other way.
    //
    // Breakpoints: compact <= 960, compact2 <= 800 (hero stacking only). The floor is 640,
    // but note a Qt minimum size is only a REQUEST - a tiling compositor ignores it, which is
    // how the overprint screenshots that started this workstream happened at all. So every
    // shed rule must still behave below 640 rather than assuming it cannot be reached.
    property int usableWidth: 1216
    readonly property bool compact:  usableWidth <= 960
    readonly property bool compact2: usableWidth <= 800
}
