#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
CLIENT_DIR="$SRC_DIR/client"
SERVER_DIR="$SRC_DIR/server"

# Destinations
CLIENT_FUNC_DIR="/home/dinda/.wine/drive_c/users/dinda/Soldier of Fortune/User/menu_store"
SERVER_FUNC_DIR="/home/dinda/.wine/drive_c/users/dinda/Soldier of Fortune/user-server/sofplus/addons"
CLIENT_MENUS_DIR="/home/dinda/.wine/drive_c/users/dinda/Soldier of Fortune/User/menus/menu_store"

mkdir -p "$CLIENT_FUNC_DIR" "$SERVER_FUNC_DIR" "$CLIENT_MENUS_DIR"

# Copy client files
if compgen -G "$CLIENT_DIR/*.func" >/dev/null; then
  cp -f "$CLIENT_DIR"/*.func "$CLIENT_FUNC_DIR"
fi
if compgen -G "$CLIENT_DIR/*.rmf" >/dev/null; then
  cp -f "$CLIENT_DIR"/*.rmf "$CLIENT_MENUS_DIR"
fi

# Copy server files
if compgen -G "$SERVER_DIR/*.func" >/dev/null; then
  cp -f "$SERVER_DIR"/*.func "$SERVER_FUNC_DIR"
fi

echo "Installed client .func → $CLIENT_FUNC_DIR"
echo "Installed client .rmf → $CLIENT_MENUS_DIR"
echo "Installed server .func → $SERVER_FUNC_DIR"


