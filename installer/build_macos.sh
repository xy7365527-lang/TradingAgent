#!/usr/bin/env bash
set -euo pipefail

# 用 PyInstaller 构建 macOS 应用与 DMG
# 依赖：pyinstaller, create-dmg (或 hdiutil)

PYINSTALLER=${PYINSTALLER:-pyinstaller}
APP_NAME=TradingAgentsGUI

echo "[1/5] 清理旧构建..."
rm -rf dist build

echo "[2/5] 使用 PyInstaller 构建 CLI 与 GUI..."
$PYINSTALLER --noconfirm --clean TradingAgents.spec
$PYINSTALLER --noconfirm --clean TradingAgentsGUI.spec

if [[ ! -d "dist/${APP_NAME}" ]]; then
  echo "ERROR: dist/${APP_NAME} 不存在，PyInstaller 构建失败" >&2
  exit 1
fi

echo "[3/5] 规范化 macOS 应用 Bundle..."
APP_BUNDLE="dist/${APP_NAME}/${APP_NAME}.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  # 单文件/目录模式时，创建简单 .app 包装
  mkdir -p "dist/${APP_NAME}.app/Contents/MacOS"
  cp -R "dist/${APP_NAME}" "dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
  APP_BUNDLE="dist/${APP_NAME}.app"
fi

echo "[4/5] 生成 DMG..."
DMG_OUT="dist/${APP_NAME}.dmg"
if command -v create-dmg >/dev/null 2>&1; then
  rm -f "$DMG_OUT"
  create-dmg --volname "${APP_NAME}" --window-pos 200 120 --window-size 600 400 \
    --icon-size 100 --app-drop-link 425 120 "$DMG_OUT" "$APP_BUNDLE"
else
  # 回退：使用 hdiutil
  rm -f "$DMG_OUT"
  hdiutil create -volname "${APP_NAME}" -srcfolder "$APP_BUNDLE" -ov -format UDZO "$DMG_OUT"
fi

echo "[5/5] 完成！输出: $DMG_OUT"





