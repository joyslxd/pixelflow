#!/usr/bin/env bash
# 用途：把本机 SSH 公钥写入博观测试机 authorized_keys；影响：之后可免密部署 PixelFlow。
# 用法：在本机执行 bash scripts/install-bg-test-ssh-key.sh
# 第一次会提示输入 root 密码，密码不会写入仓库或日志。

set -euo pipefail

SERVER="${PIXELFLOW_BG_TEST_SSH:-root@115.191.36.147}"
PUBKEY="${PIXELFLOW_BG_TEST_PUBKEY:-$HOME/.ssh/id_ed25519.pub}"

if [[ ! -f "$PUBKEY" ]]; then
  echo "找不到公钥：$PUBKEY" >&2
  exit 1
fi

if [[ ! -x "$(command -v ssh-copy-id)" ]]; then
  echo "本机缺少 ssh-copy-id" >&2
  exit 1
fi

echo "将把公钥装到 ${SERVER}："
cat "$PUBKEY"
echo
ssh-copy-id -i "$PUBKEY" -o StrictHostKeyChecking=accept-new "$SERVER"

echo
echo "验证免密登录："
ssh -o BatchMode=yes -o ConnectTimeout=8 "$SERVER" 'echo ok; hostname; whoami'
echo "密钥已生效，可以说一声继续推送 PixelFlow。"
