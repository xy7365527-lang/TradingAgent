Param(
  [string]$Python = "python",
  [string]$PyInstaller = "pyinstaller",
  [string]$InnoSetupCompiler = "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe"
)

$ErrorActionPreference = "Stop"

Write-Host '[1/4] 清理旧构建...'
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue | Out-Null

Write-Host '[2/4] 使用 PyInstaller 构建 CLI 与 GUI...'
& $PyInstaller --noconfirm --clean TradingAgents.spec
& $PyInstaller --noconfirm --clean TradingAgentsGUI.spec

if (-not (Test-Path "dist/TradingAgentsGUI/TradingAgentsGUI.exe")) {
  Write-Error "未找到 dist/TradingAgentsGUI/TradingAgentsGUI.exe，PyInstaller 构建失败。"
}

Write-Host '[3/4] 使用 Inno Setup 生成安装包...'
if (-not (Test-Path $InnoSetupCompiler)) {
  Write-Warning ('未找到 Inno Setup 编译器：' + $InnoSetupCompiler)
  Write-Warning '请安装 Inno Setup 6 或手动打开 installer/windows/TradingAgents.iss 进行编译。'
} else {
  & $InnoSetupCompiler "installer/windows/TradingAgents.iss"
}

Write-Host '[4/4] 完成！安装包输出在 Output 目录（由 Inno Setup 生成）。'

