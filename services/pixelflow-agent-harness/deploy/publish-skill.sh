#!/usr/bin/env bash
# 用途：管理员把已校验 Skill 原子发布到活动根；影响：只影响发布完成后的新 Run。
set -euo pipefail

root="${PIXELFLOW_AGENT_HOME:?请注入活动 Skill 根}"
name="${1:?请提供 kebab-case Skill 名称}"
source="${2:?请提供待发布 SKILL.md 路径}"
if [[ ! "$name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "Skill 名称必须为 kebab-case" >&2; exit 2
fi
if [[ ! -f "$source" ]] || [[ $(wc -c < "$source") -gt 32768 ]]; then
  echo "Skill 文件不存在或超过 32768 字节预算" >&2; exit 2
fi
if ! awk 'NR==1 {start=($0=="---")} /^description:/ {description=1} /^---$/ && NR>1 {end=1} END {exit !(start && description && end)}' "$source"; then
  echo "Skill 必须包含带 description 的 frontmatter" >&2; exit 2
fi
target="$root/skills/$name"
mkdir -p "$target"
temporary="$target/.SKILL.md.$$.tmp"
install -m 0400 "$source" "$temporary"
mv -f "$temporary" "$target/SKILL.md"
