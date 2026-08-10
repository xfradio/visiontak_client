# Headless Wayland harness for the kiosk UI.
#
# Ubuntu Frame is a snap and cannot run in a plain container, but the client is an
# ordinary Wayland client — so Weston's headless backend stands in for Frame and
# exercises the same code path: GTK4 surface, WebKitGTK views, CSS, key handling.
#
# Same Ubuntu release as the snap's core24 base, and the same distro GTK/WebKit
# packages snapcraft stages, so an import or ABI mismatch shows up here.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 \
      python3-pip \
      python3-gi \
      python3-gi-cairo \
      gir1.2-gtk-4.0 \
      gir1.2-webkit-6.0 \
      libwebkitgtk-6.0-4 \
      weston \
      libgl1-mesa-dri \
      libegl-mesa0 \
      fonts-dejavu-core \
      dbus \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Software rendering: no GPU in CI, and WebKit's bubblewrap sandbox cannot nest
# inside a container any more than it can inside snap confinement.
ENV LIBGL_ALWAYS_SOFTWARE=1 \
    GALLIUM_DRIVER=llvmpipe \
    WEBKIT_DISABLE_COMPOSITING_MODE=1 \
    WEBKIT_FORCE_SANDBOX=0 \
    GDK_BACKEND=wayland \
    XDG_RUNTIME_DIR=/tmp/xdg \
    PYTHONPATH=/w/src

WORKDIR /w
COPY docker/headless-run.sh /usr/local/bin/headless-run
RUN chmod +x /usr/local/bin/headless-run

ENTRYPOINT ["/usr/local/bin/headless-run"]
