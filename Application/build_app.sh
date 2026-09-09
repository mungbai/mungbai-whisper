#!/bin/zsh
set -euo pipefail

project_root="${0:A:h:h}"
cd "$project_root"

xcrun swiftc -swift-version 5 -O \
  -framework AppKit \
  -framework Foundation \
  "Application/Source/macOS App/main.swift" \
  -o "Transcribe Audio.app/Contents/MacOS/TranscribeAudio"

cp "Application/Source/macOS App/Info.plist" "Transcribe Audio.app/Contents/Info.plist"
mkdir -p "Transcribe Audio.app/Contents/Resources"
cp "Application/Resources/AppIcon.icns" "Transcribe Audio.app/Contents/Resources/AppIcon.icns"
chmod +x "Transcribe Audio.app/Contents/MacOS/TranscribeAudio"
codesign --force --deep --sign - "Transcribe Audio.app"
