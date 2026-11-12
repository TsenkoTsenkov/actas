#!/bin/sh
# install.sh - install actas (one Python file) into ~/.local/bin.
#
#   curl -fsSLO https://raw.githubusercontent.com/TsenkoTsenkov/actas/main/install.sh
#   sh install.sh                     # latest release
#   sh install.sh --version v2.0.0    # a given release
#
# It downloads the release asset `actas` and `SHA256SUMS`, checks the
# SHA-256, and installs the file with mode 0755. It never uses sudo.
# If the destination is a symlink (for example into a git checkout), it
# refuses unless --force, and then replaces the link itself; it never writes
# through a link.
#
# The checksum guards against a broken or truncated download. To check that
# the file was built by this repository's release workflow, run the
# `gh attestation verify` command printed at the end.
#
# Options and environment:
#   --version TAG   release tag, or "latest" (ACTAS_VERSION; default latest)
#   --dir DIR       install directory (ACTAS_INSTALL_DIR; default ~/.local/bin)
#   --repo O/N      GitHub repository (ACTAS_REPO; default TsenkoTsenkov/actas)
#   --force         replace a symlink at DIR/actas
# The pre-rename CCENV_VERSION, CCENV_INSTALL_DIR and CCENV_REPO are read
# when the ACTAS_ ones are unset (legacy).

set -eu

REPO=${ACTAS_REPO:-${CCENV_REPO:-TsenkoTsenkov/actas}}
VERSION=${ACTAS_VERSION:-${CCENV_VERSION:-latest}}
DIR=${ACTAS_INSTALL_DIR:-${CCENV_INSTALL_DIR:-}}
FORCE=0
TMP=
STAGE=

say() { printf 'actas-install: %s\n' "$*"; }
die() { printf 'actas-install: error: %s\n' "$*" >&2; exit 1; }

usage() {
	cat <<'EOF'
usage: sh install.sh [--version TAG] [--dir DIR] [--repo OWNER/NAME] [--force]

Installs the actas release asset (SHA-256 checked) as DIR/actas, mode 0755.
  --version TAG   release tag such as v2.0.0, or latest (default; ACTAS_VERSION)
  --dir DIR       install directory (default ~/.local/bin; ACTAS_INSTALL_DIR)
  --repo O/N      GitHub repository (default TsenkoTsenkov/actas; ACTAS_REPO)
  --force         replace a symlink at DIR/actas (the link itself, never its target)
Never uses sudo.
EOF
}

need_value() { [ "$2" -ge 2 ] || die "$1 needs a value"; }

while [ $# -gt 0 ]; do
	case $1 in
		--version) need_value "$1" $#; VERSION=$2; shift 2 ;;
		--version=*) VERSION=${1#*=}; shift ;;
		--dir) need_value "$1" $#; DIR=$2; shift 2 ;;
		--dir=*) DIR=${1#*=}; shift ;;
		--repo) need_value "$1" $#; REPO=$2; shift 2 ;;
		--repo=*) REPO=${1#*=}; shift ;;
		--force) FORCE=1; shift ;;
		-h | --help) usage; exit 0 ;;
		*) die "unknown option: $1 (see --help)" ;;
	esac
done

if [ -z "$DIR" ]; then
	[ -n "${HOME:-}" ] || die "HOME is not set; pass --dir"
	DIR=$HOME/.local/bin
fi

case $REPO in
	*[!A-Za-z0-9._/-]* | /* | */ | *//* | */*/* | "") die "bad --repo: $REPO (want OWNER/NAME)" ;;
	*/*) ;;
	*) die "bad --repo: $REPO (want OWNER/NAME)" ;;
esac

case $VERSION in
	latest) BASE=https://github.com/$REPO/releases/latest/download ;;
	*[!A-Za-z0-9._+-]* | "") die "bad --version: $VERSION" ;;
	[0-9]*) VERSION=v$VERSION; BASE=https://github.com/$REPO/releases/download/$VERSION ;;
	v[0-9]*) BASE=https://github.com/$REPO/releases/download/$VERSION ;;
	*) die "bad --version: $VERSION (want a tag like v2.0.0, or latest)" ;;
esac

command -v curl >/dev/null 2>&1 || die "curl is required"
if command -v sha256sum >/dev/null 2>&1; then
	sha256() { sha256sum "$1" | awk '{ print $1 }'; }
elif command -v shasum >/dev/null 2>&1; then
	sha256() { shasum -a 256 "$1" | awk '{ print $1 }'; }
else
	die "sha256sum or shasum is required"
fi

cleanup() {
	[ -z "$TMP" ] || rm -rf "$TMP"
	[ -z "$STAGE" ] || rm -f "$STAGE"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

TMP=$(mktemp -d "${TMPDIR:-/tmp}/actas-install.XXXXXX") || die "cannot create a temp directory"

fetch() {
	curl -fsSL --proto '=https' --proto-redir '=https' --retry 2 -o "$2" "$1" && return
	[ "$VERSION" != latest ] || die "download failed: $1 (latest is the newest stable release; for a prerelease pass --version TAG, e.g. v2.0.0b1)"
	die "download failed: $1"
}

say "downloading actas $VERSION from github.com/$REPO"
fetch "$BASE/actas" "$TMP/actas"
fetch "$BASE/SHA256SUMS" "$TMP/SHA256SUMS"

want=$(awk '$2 == "actas" || $2 == "*actas" { print $1; exit }' "$TMP/SHA256SUMS" | tr 'A-F' 'a-f')
case $want in
	*[!0-9a-f]* | "") die "SHA256SUMS has no valid entry for actas; nothing was installed" ;;
esac
[ ${#want} -eq 64 ] || die "SHA256SUMS has no valid entry for actas; nothing was installed"
got=$(sha256 "$TMP/actas")
[ "$got" = "$want" ] ||
	die "checksum mismatch for actas (expected $want, got $got); nothing was installed"
head -n 1 "$TMP/actas" | grep -q '^#!.*python' ||
	die "the download is not the actas script; nothing was installed"
say "sha256 ok: $got"

DEST=$DIR/actas
if [ -L "$DEST" ]; then
	if [ "$FORCE" -ne 1 ]; then
		target=$(readlink "$DEST" 2>/dev/null || echo '?')
		die "$DEST is a symlink (to $target), e.g. into a git checkout. Not writing through it. Re-run with --force to replace the link itself, or update the checkout instead."
	fi
elif [ -d "$DEST" ]; then
	die "$DEST is a directory"
elif [ -e "$DEST" ] && [ ! -f "$DEST" ]; then
	die "$DEST exists and is not a regular file"
fi

mkdir -p "$DIR" || die "cannot create $DIR"
[ -w "$DIR" ] || die "$DIR is not writable (this installer never uses sudo; choose another --dir)"

# Stage next to the destination, then rename: the swap is atomic and a
# rename replaces a symlink itself, never the file it points to.
STAGE=$(mktemp "$DIR/.actas.XXXXXX") || die "cannot write to $DIR"
cat "$TMP/actas" >"$STAGE"
chmod 0755 "$STAGE"
if [ -L "$DEST" ]; then
	rm -f "$DEST" # removes the link only; mv would follow a link to a directory
fi
mv -f "$STAGE" "$DEST"
STAGE=

if command -v python3 >/dev/null 2>&1; then
	say "installed $DEST ($(python3 "$DEST" --version 2>/dev/null || echo 'version unknown'))"
	python3 -c 'import yaml' 2>/dev/null || command -v yq >/dev/null 2>&1 ||
		say "no PyYAML or yq: actas init writes a JSON manifest (for YAML: python3 -m pip install --user pyyaml, or brew install yq)"
else
	say "installed $DEST; actas needs Python 3.9+ (python3 was not found on PATH)"
fi
case ":${PATH:-}:" in
	*":$DIR:"*) ;;
	*) say "$DIR is not on your PATH; add it, e.g.: export PATH=\"$DIR:\$PATH\"" ;;
esac
# Legacy: actas was called ccenv. An old install keeps working (synced
# settings files may still call it); it is left alone.
if [ -e "$DIR/ccenv" ] || [ -L "$DIR/ccenv" ]; then
	say "$DIR/ccenv is the old (pre-rename) install and still works; run actas sync so synced files call actas, then remove it, or keep the old name as an alias: ln -sf actas '$DIR/ccenv'"
fi
say "optional authenticity check: gh attestation verify '$DEST' --repo $REPO"
say "next: actas init && actas doctor"
