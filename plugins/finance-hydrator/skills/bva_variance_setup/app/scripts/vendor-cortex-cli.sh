#!/bin/sh
# Downloads and verifies the Cortex Code (CoCo) CLI so it can be deployed
# alongside this app, WITHOUT any network access from inside the Snowflake
# App Runtime build container.
#
# Why this exists: the App Runtime build container ships without curl/wget or a
# package manager, and outbound network access is restricted during the build
# phase, so `install:` in app.yml can never fetch the
# CLI itself. Instead, run this script HERE, on your own machine (which has
# normal internet access), BEFORE `snow app deploy`. The resulting tarball
# gets uploaded as part of the normal deploy artifacts (snowflake.yml's
# `artifacts: src: ./*`), and app.yml's `install:` step just extracts it
# locally with `tar` — no network call needed at build time at all.
#
# Run this once before your first deploy, and again any time you want to
# bump the CoCo CLI version. Safe to re-run — it's a no-op if a
# checksum-verified tarball is already present.
#
# Usage: sh scripts/vendor-cortex-cli.sh [--channel stable|dev|beta]

set -e

CHANNEL="${CORTEX_CHANNEL:-stable}"
if [ "$1" = "--channel" ] && [ -n "$2" ]; then
    CHANNEL="$2"
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
VENDOR_DIR="$SCRIPT_DIR/../vendor"
# The whole, unsplit tarball is cached OUTSIDE the project directory entirely
# (not just gitignored) — see the split_vendor_file() comment below for why.
CACHE_DIR="${VENDOR_CACHE_DIR:-$HOME/.cache/bva-cortex-cli}"
TARGET_FILE="$CACHE_DIR/coco-linux-amd64.tar.gz"

# The CLI always runs inside a Linux (glibc) App Runtime container regardless
# of what OS this script runs on, so the platform is fixed to linux/amd64 —
# NOT auto-detected from the local machine.
OS="linux"
ARCH="amd64"

# Set these to your Cortex Code CLI download location before running.
S3_BUCKET="${COCO_CLI_HOST:-<CLI_DOWNLOAD_HOST>}"
S3_PREFIX="${COCO_CLI_PREFIX:-<CLI_DOWNLOAD_PREFIX>}"

print_error() { echo "Error: $1" >&2; }

check_dependencies() {
    for cmd in curl tar; do
        if ! command -v "$cmd" > /dev/null 2>&1; then
            print_error "$cmd is required but not installed on this machine."
            exit 1
        fi
    done
    if ! command -v shasum > /dev/null 2>&1 && ! command -v sha256sum > /dev/null 2>&1; then
        print_error "Neither shasum nor sha256sum found — cannot verify the download."
        exit 1
    fi
}

sha256_of() {
    if command -v shasum > /dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        sha256sum "$1" | awk '{print $1}'
    fi
}

url_encode_plus() {
    echo "$1" | sed 's/+/%2B/g'
}

main() {
    check_dependencies
    mkdir -p "$VENDOR_DIR"
    mkdir -p "$CACHE_DIR"

    echo "Fetching latest ${CHANNEL} CoCo CLI version..."
    version_url="https://${S3_BUCKET}/${S3_PREFIX}/${CHANNEL}_version.txt"
    version=$(curl -fsSL "$version_url" | tr -d '[:space:]')
    if [ -z "$version" ]; then
        print_error "Failed to determine the latest ${CHANNEL} version from $version_url"
        exit 1
    fi
    echo "Version: $version"

    encoded_version=$(url_encode_plus "$version")
    manifest_url="https://${S3_BUCKET}/${S3_PREFIX}/${encoded_version}/manifest.json"
    manifest=$(curl -fsSL "$manifest_url")
    if [ -z "$manifest" ]; then
        print_error "Failed to download manifest from $manifest_url"
        exit 1
    fi

    if command -v jq > /dev/null 2>&1; then
        tar_name=$(echo "$manifest" | jq -r ".packages.${OS}.${ARCH}.name")
        expected_checksum=$(echo "$manifest" | jq -r ".packages.${OS}.${ARCH}.checksum")
    else
        tar_name=$(echo "$manifest" | grep -o "\"name\"[[:space:]]*:[[:space:]]*\"[^\"]*${OS}-${ARCH}\.tar\.gz\"" | grep -o '"[^"]*\.tar\.gz"' | tr -d '"' | head -1)
        expected_checksum=$(echo "$manifest" | grep -A2 "\"${tar_name}\"" | grep -o '"checksum"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4 | head -1)
    fi

    if [ -z "$tar_name" ] || [ "$tar_name" = "null" ] || [ -z "$expected_checksum" ] || [ "$expected_checksum" = "null" ]; then
        print_error "Could not resolve a ${OS}-${ARCH} package from the manifest."
        exit 1
    fi

    # Skip re-downloading if we already have a checksum-verified copy.
    # (Falls through to split_vendor_file below rather than exiting, so
    # chunks always get (re)created even when the download itself is skipped.)
    if [ -f "$TARGET_FILE" ]; then
        existing_checksum=$(sha256_of "$TARGET_FILE")
        if [ "$existing_checksum" = "$expected_checksum" ]; then
            echo "Already have a verified copy of $tar_name at $TARGET_FILE — skipping download."
            return 0
        fi
        echo "Existing $TARGET_FILE doesn't match the current manifest checksum — re-downloading."
    fi

    encoded_tar_name=$(url_encode_plus "$tar_name")
    tar_url="https://${S3_BUCKET}/${S3_PREFIX}/${encoded_version}/${encoded_tar_name}"
    echo "Downloading $tar_name..."
    curl -fL --progress-bar "$tar_url" -o "$TARGET_FILE.tmp"

    actual_checksum=$(sha256_of "$TARGET_FILE.tmp")
    if [ "$actual_checksum" != "$expected_checksum" ]; then
        print_error "Checksum mismatch: expected $expected_checksum, got $actual_checksum"
        rm -f "$TARGET_FILE.tmp"
        exit 1
    fi

    mv "$TARGET_FILE.tmp" "$TARGET_FILE"
    echo "Verified and saved to $TARGET_FILE ($(du -h "$TARGET_FILE" | cut -f1))"
}

# Splits the verified tarball into small chunks INSIDE the project's vendor/
# directory (so `snow app deploy` uploads them normally), and re-runs even
# when the download was skipped (checksum already matched) so chunks always
# exist and stay in sync with TARGET_FILE.
#
# Why the whole tarball lives OUTSIDE the project tree (in CACHE_DIR) rather
# than just being excluded via snowflake.yml's `artifacts.ignore`: excluding
# it via `ignore` was tried first and caused `snow app deploy` to loop
# forever retrying a multipart upload chunk for that exact (correctly
# excluded, physically absent from the bundle) file — an infinite
# `FileNotFoundError` retry loop with no visible error until Ctrl+C, not a
# genuine hang. Keeping the whole file out of the project directory
# entirely sidesteps that bug: there is no path for the tool to ever
# reference it, ignored or not. Only the small `.part-*` chunks — real,
# present files — live in vendor/ and upload like any other project file.
split_vendor_file() {
    CHUNK_SIZE="${VENDOR_CHUNK_SIZE:-20m}"
    CHUNK_PREFIX="$VENDOR_DIR/coco-linux-amd64.tar.gz.part-"
    echo "Splitting into ${CHUNK_SIZE} chunks for upload..."
    rm -f "$CHUNK_PREFIX"*
    split -b "$CHUNK_SIZE" "$TARGET_FILE" "$CHUNK_PREFIX"
    part_count=$(ls "$CHUNK_PREFIX"* 2>/dev/null | wc -l | tr -d ' ')
    echo "Created $part_count chunk(s) at ${CHUNK_PREFIX}*"
    echo "Ready to deploy — app.yml's install: step reassembles and extracts these locally at build time."
}

main
split_vendor_file
