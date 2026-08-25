#!/usr/bin/env bash
# Build and install the engine + control panel for the current user.
#
#   bash install.sh
#
# Tested on CachyOS (KDE Plasma and Hyprland). It should work on any current
# Arch-family system; other distributions are not covered yet.
#
# Nothing here is installed system-wide except the packages pacman fetches.
# The engine lands in ~/.local/lib/lwe-engine, the commands in ~/.local/bin,
# and the panel in its own virtualenv under ~/.local/share/lwe-ui.
set -euo pipefail

VENV="$HOME/.local/share/lwe-ui/venv"
ENGINE_HOME="$HOME/.local/lib/lwe-engine"

if [ ! -f CMakeLists.txt ] || [ ! -d lwe-ui ]; then
    echo "run this from the repository root (CMakeLists.txt + lwe-ui/ expected)" >&2
    exit 1
fi

# The texture compressor is built through ispc, which targets x86-64. The rest
# of the engine is not known to work anywhere else because it has never been
# tested there, so refuse early instead of failing deep in the build.
if [ "$(uname -m)" != "x86_64" ]; then
    echo "This project is x86-64 only. Detected: $(uname -m)." >&2
    exit 1
fi

if ! command -v pacman >/dev/null 2>&1; then
    echo "This installer is for Arch-family systems (it uses pacman)." >&2
    echo "On other distributions, install the dependencies listed in the README" >&2
    echo "and build with cmake by hand." >&2
    exit 1
fi

echo "== step 1/6: dependencies =="
echo "pacman will list what it plans to install and ask you to confirm. Nothing"
echo "is installed without your approval."
sudo pacman -S --needed \
    base-devel cmake git pkgconf \
    mesa glu glew freeglut glfw \
    libx11 libxext libxrandr libxi libxmu libsm libice \
    wayland wayland-protocols dbus \
    lz4 freetype2 \
    sdl2-compat ffmpeg mpv libpulse \
    python pyside6 python-pillow ispc

echo
echo "== step 2/6: configuring the build =="
echo "This downloads the Chromium Embedded Framework, which is a few hundred"
echo "megabytes, so the first run takes a while on a slow connection. This is"
echo "necessary to run WEB wallpapers, which run in a CEF environment."
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release

# A build without the Wayland driver starts and then cannot drive any output,
# which looks like a black screen rather than an error. Catch it here instead.
if ! grep -q "^WAYLAND_SUPPORT_FOUND:INTERNAL=1" build/CMakeCache.txt; then
    echo "ERROR: Wayland support was not detected by the configure step." >&2
    echo "Install wayland and wayland-protocols, then re-run install.sh." >&2
    exit 1
fi

echo
echo "== step 3/6: compiling =="
echo "This is the long part. It uses all cores available."
make -C build -j"$(nproc)"

echo
echo "== step 4/6: installing the engine to ~/.local =="
mkdir -p "$ENGINE_HOME" "$HOME/.local/bin"
cp -a build/output/. "$ENGINE_HOME/"
ln -sf "$ENGINE_HOME/linux-wallpaperengine" "$HOME/.local/bin/linux-wallpaperengine"
ln -sf "$ENGINE_HOME/lwe-web-service" "$HOME/.local/bin/lwe-web-service"

if [ ! -f "$ENGINE_HOME/lwe_bc7enc" ]; then
    echo "ERROR: the texture-compression tool did not build." >&2
    echo "It needs ispc, which the dependency step installs." >&2
    exit 1
fi
ln -sf "$ENGINE_HOME/lwe_bc7enc" "$HOME/.local/bin/lwe_bc7enc"

echo
echo "== step 5/6: installing the control panel =="
# The panel goes in a virtualenv rather than into the system Python, which Arch
# marks as externally managed. --system-site-packages lets it import the Qt
# bindings pacman just installed instead of pulling a second copy from PyPI.
echo "The panel installs into its own virtualenv at:"
echo "  $VENV"
echo "It reuses the system PySide6 rather than downloading its own copy of Qt."
python -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade ./lwe-ui
ln -sf "$VENV/bin/lwe-ui" "$HOME/.local/bin/lwe-ui"

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "NOTE: add ~/.local/bin to your PATH to run the installed commands." ;;
esac

echo
echo "== step 6/6: adding the application menu entry =="
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/lwe-ui.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=LWE Control Panel
Comment=Wallpaper engine control panel
Exec=$HOME/.local/bin/lwe-ui
Icon=preferences-desktop-wallpaper
Categories=Settings;Utility;
Terminal=false
DESKTOP
# desktop environments cache the menu; refresh whichever cache tool exists
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
kbuildsycoca6 2>/dev/null || kbuildsycoca5 2>/dev/null || true

echo
echo "Done. Start the panel with: lwe-ui"
echo "On first run, open Settings to point it at your Wallpaper Engine assets"
echo "(a Steam install of Wallpaper Engine is required for the assets)."
