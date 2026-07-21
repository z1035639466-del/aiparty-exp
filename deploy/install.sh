#!/bin/zsh
# Yappa 公网部署一键安装(幂等:重复执行安全,已装好的步骤会自动跳过)。
# 适用:macOS(Apple Silicon / arm64),无 brew、无 docker、无 sudo。
# 用法:cd 到仓库根目录后执行  zsh deploy/install.sh
# 手动逐行执行的等价命令见 deploy/RUNBOOK.md(那份不带行内注释,放心整段粘贴)。

set -e
set -u

CADDY_VERSION="2.10.0"
CADDY_TGZ="caddy_${CADDY_VERSION}_mac_arm64.tar.gz"
CADDY_URL="https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/${CADDY_TGZ}"
CADDY_SUMS_URL="https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_checksums.txt"

# 仓库根 = 本脚本所在目录的上一级,不依赖执行时的 cwd
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/Library/Logs/yappa"
BIN_DIR="$HOME/bin"
AGENTS_DIR="$HOME/Library/LaunchAgents"

echo "==> [1/6] 仓库目录:$REPO_DIR"

echo "==> [2/6] 创建日志目录 $LOG_DIR"
mkdir -p "$LOG_DIR"

echo "==> [3/6] 安装 caddy 到 $BIN_DIR/caddy(官方静态二进制,无 brew)"
mkdir -p "$BIN_DIR"
if "$BIN_DIR/caddy" version 2>/dev/null | grep -q "v${CADDY_VERSION}"; then
    echo "    已存在 caddy v${CADDY_VERSION},跳过下载"
else
    TMP_DIR="$(mktemp -d)"
    echo "    下载 $CADDY_URL"
    curl -fsSL -o "$TMP_DIR/$CADDY_TGZ" "$CADDY_URL"
    echo "    下载校验清单并核对 SHA-256"
    curl -fsSL -o "$TMP_DIR/checksums.txt" "$CADDY_SUMS_URL"
    (
        cd "$TMP_DIR"
        grep " ${CADDY_TGZ}\$" checksums.txt | shasum -a 256 -c -
    )
    echo "    校验通过,解压安装"
    tar -xzf "$TMP_DIR/$CADDY_TGZ" -C "$TMP_DIR" caddy
    mv "$TMP_DIR/caddy" "$BIN_DIR/caddy"
    chmod +x "$BIN_DIR/caddy"
    rm -rf "$TMP_DIR"
    echo "    完成:$("$BIN_DIR/caddy" version)"
fi

echo "==> [4/6] 校验 Caddyfile 配置"
"$BIN_DIR/caddy" validate --config "$REPO_DIR/deploy/Caddyfile" >/dev/null
if grep -q "yappa.example.com" "$REPO_DIR/deploy/Caddyfile"; then
    echo "    警告:Caddyfile 里还是占位域名 yappa.example.com,记得换成你自己的域名"
fi

echo "==> [5/6] 安装 launchd 服务(plist 里的占位路径自动替换为 $HOME)"
mkdir -p "$AGENTS_DIR"
for name in com.yappa.server com.yappa.caddy; do
    sed "s|/Users/YOUR_USERNAME/aiparty-exp|$REPO_DIR|g; s|/Users/YOUR_USERNAME|$HOME|g" \
        "$REPO_DIR/deploy/$name.plist" > "$AGENTS_DIR/$name.plist"
    echo "    已写入 $AGENTS_DIR/$name.plist"
done

echo "==> [6/6] 加载并启动服务(已在跑的先卸载再加载,保证吃到新配置)"
for name in com.yappa.server com.yappa.caddy; do
    launchctl unload "$AGENTS_DIR/$name.plist" 2>/dev/null || true
    launchctl load "$AGENTS_DIR/$name.plist"
    echo "    $name 已加载"
done

echo ""
echo "全部完成。接下来:"
echo "  1) 确认 .env 里已配置 PUBLIC_BASE_URL=https://你的域名"
echo "  2) 验证:curl https://你的域名/api/state"
echo "  3) 日志在 $LOG_DIR/,排障与日常运维见 deploy/RUNBOOK.md"
