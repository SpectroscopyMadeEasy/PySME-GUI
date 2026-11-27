# Layout of the installer window, read by dmgbuild (see build.sh). It writes the
# layout into the disk image directly, without opening Finder.
# The window size and icon positions must match make_dmg_background.swift.
# ruff: noqa: F821  (dmgbuild provides "defines")

import os.path

app = defines["app"]
name = os.path.basename(app)

format = "UDZO"
files = [app]
symlinks = {"Applications": "/Applications"}
icon = defines["volume_icon"]

background = defines["background"]          # the @2x file beside it is picked up
window_rect = ((240, 180), (640, 400))
default_view = "icon-view"
show_toolbar = False
show_sidebar = False
show_status_bar = False
show_tab_view = False
show_pathbar = False
arrange_by = None
icon_size = 128
text_size = 14
# Centres of the icons: the app on the left, as Mac users expect.
icon_locations = {name: (170, 175), "Applications": (470, 175)}
# Do not set hide_extensions: it marks the app itself with Finder information,
# which a signed app may not carry ("codesign --verify --strict" then fails).
# Finder normally hides ".app" anyway.
