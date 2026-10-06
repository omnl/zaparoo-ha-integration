#!/bin/sh
# Run from the unpacked release directory on Batocera.
set -eu
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DEST=/userdata/system/kidsstation
case "$(uname -m)" in
    x86_64) ARCH=amd64 ;;
    aarch64) ARCH=arm64 ;;
    *) echo "Supported architectures: x86_64 and aarch64"; exit 1 ;;
esac
mkdir -p "$DEST" /userdata/system/services
cp "$SOURCE/dist/kidsstation-agent-linux-$ARCH" "$DEST/kidsstation-agent.new"
chmod 755 "$DEST/kidsstation-agent.new"
mv "$DEST/kidsstation-agent.new" "$DEST/kidsstation-agent"
if [ ! -e "$DEST/config.json" ]; then
    cp "$SOURCE/agent/config.example.json" "$DEST/config.json"
    TOKEN=$("$DEST/kidsstation-agent" -generate-token)
    sed -i "s/GENERATE_A_TOKEN_WITH_THE_AGENT/$TOKEN/" "$DEST/config.json"
fi
chmod 600 "$DEST/config.json"
cp "$SOURCE/batocera/services/kidsstation" /userdata/system/services/kidsstation
chmod 755 /userdata/system/services/kidsstation
batocera-services enable kidsstation
batocera-services restart kidsstation
echo "Agent installed. Add the Zaparoo hook shown in docs/INSTALL_DE.md."
echo "For Home Assistant, copy the token from $DEST/config.json."
