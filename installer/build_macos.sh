#!/usr/bin/env bash
set -euo pipefail

# 用 PyInstaller 构建 macOS 应用与 DMG
# 依赖：pyinstaller, create-dmg (或 hdiutil)

PYINSTALLER=${PYINSTALLER:-pyinstaller}
APP_NAME=TradingAgentsGUI
SPEC_CLI=TradingAgents.spec
SPEC_GUI=TradingAgentsGUI.spec

# 版本号获取：优先环境变量 APP_VERSION，其次读取 pyproject.toml，最后回退为 0.0.0
VERSION=${APP_VERSION:-}
if [[ -z "$VERSION" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    VERSION=$(python3 - << 'PY'
import sys, re
try:
  with open('pyproject.toml', 'r', encoding='utf-8') as f:
    s = f.read()
  m = re.search(r"^version\s*=\s*\"([0-9]+\.[0-9]+\.[0-9]+)\"", s, flags=re.M)
  if m:
    print(m.group(1))
  else:
    print("0.0.0")
except Exception:
  print("0.0.0")
PY
)
  else
    VERSION="0.0.0"
  fi
fi

echo "[1/5] 清理旧构建..."
rm -rf dist build

echo "[2/5] 使用 PyInstaller 构建 CLI 与 GUI..."
$PYINSTALLER --noconfirm --clean "$SPEC_CLI" || true
$PYINSTALLER --noconfirm --clean "$SPEC_GUI"

if [[ ! -d "dist/${APP_NAME}" && ! -d "dist/${APP_NAME}.app" ]]; then
  echo "ERROR: dist/${APP_NAME} 不存在，PyInstaller 构建失败" >&2
  exit 1
fi

echo "[3/5] 规范化 macOS 应用 Bundle..."
APP_BUNDLE="dist/${APP_NAME}.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  # 如果 PyInstaller 没有输出 .app（例如是目录/单文件），则包一层 .app
  if [[ -d "dist/${APP_NAME}" ]]; then
    mkdir -p "dist/${APP_NAME}.app/Contents/MacOS"
    cp -R "dist/${APP_NAME}" "dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
  else
    echo "ERROR: 未找到可用于打包的 GUI 产物" >&2
    exit 1
  fi
fi

echo "[4/5] 生成 DMG..."
DMG_OUT="dist/${APP_NAME}-${VERSION}.dmg"
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





