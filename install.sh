#!/bin/sh
set -eu

REPOSITORY="mahmoudchaer/Inference-Gateway"
BASE_URL="https://github.com/$REPOSITORY/releases/latest/download"
DEFAULT_INSTALL_DIR="$HOME/.local/bin"
INSTALL_DIR="${INFERENCE_GATEWAY_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) artifact="inference-gateway-macos-arm64" ;;
  Darwin-x86_64) artifact="inference-gateway-macos-x64" ;;
  *) echo "Inference Gateway currently supports macOS and Windows." >&2; exit 1 ;;
esac

temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT HUP INT TERM

echo "Downloading Inference Gateway..."
# Keep curl's compact, familiar progress bar visible even when this installer is
# launched through `curl ... | sh`. Errors still fail the installation.
curl -fL --progress-bar "$BASE_URL/$artifact" -o "$temporary/inference-gateway"
echo "Verifying download..."
curl -fsSL "$BASE_URL/checksums.txt" -o "$temporary/checksums.txt"
expected="$(awk -v name="$artifact" '$2 == name {print $1}' "$temporary/checksums.txt")"
actual="$(shasum -a 256 "$temporary/inference-gateway" | awk '{print $1}')"
if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
  echo "Checksum verification failed; nothing was installed." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
chmod 755 "$temporary/inference-gateway"
mv "$temporary/inference-gateway" "$INSTALL_DIR/inference-gateway"

case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *)
    if [ "$INSTALL_DIR" != "$DEFAULT_INSTALL_DIR" ]; then
      echo "Add $INSTALL_DIR to your PATH to use inference-gateway."
    else
      profile="$HOME/.zprofile"
      [ -n "${SHELL:-}" ] && [ "$(basename "$SHELL")" = "bash" ] && profile="$HOME/.bash_profile"
      marker='# Added by Inference Gateway installer'
      if ! grep -Fq "$marker" "$profile" 2>/dev/null; then
        printf '\n%s\nexport PATH="$HOME/.local/bin:$PATH"\n' "$marker" >> "$profile"
      fi
      export PATH="$INSTALL_DIR:$PATH"
      echo "Added $INSTALL_DIR to your PATH."
    fi
    ;;
esac

printf '\nInstalled Inference Gateway.\n\nNext steps:\n'
printf '  1. Set your API key:  inference-gateway config --api-key YOUR_JEV_KEY\n'
printf '  2. Start the gateway:  inference-gateway start\n'
printf '  3. See all commands:   inference-gateway help\n'
