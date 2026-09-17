#!/usr/bin/env bash
# dev.sh — 一屏三格启动 calc_client / app / sdk
#
# 布局：
#   ┌──────────────────────┬───────────┐
#   │                      │  app.py   │
#   │    calc_client.py    ├───────────┤
#   │      (60% 宽)        │  sdk.py   │
#   └──────────────────────┴───────────┘
#
# 用法：
#   ./dev.sh          # 创建/复用 session 并启动
#   ./dev.sh attach   # 只 attach
#   ./dev.sh kill     # 杀掉 session

set -euo pipefail

SESSION="calc"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv/bin/activate"

# 颜色
C_TITLE='\033[1;36m'
C_DIM='\033[0;90m'
C_RESET='\033[0m'

# ---- 子命令 ----
case "${1:-start}" in
  kill)
    tmux kill-session -t "$SESSION" 2>/dev/null && \
      echo -e "${C_DIM}killed session: $SESSION${C_RESET}" || \
      echo -e "${C_DIM}no session: $SESSION${C_RESET}"
    exit 0
    ;;
  attach)
    tmux attach -t "$SESSION" 2>/dev/null || {
      echo -e "${C_DIM}session 不存在，先跑 ./dev.sh${C_RESET}"
      exit 1
    }
    exit 0
    ;;
  start) ;;
  *)
    echo "用法: $0 [start|attach|kill]" >&2
    exit 1
    ;;
esac

# ---- 如果已有 session，直接 attach ----
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo -e "${C_DIM}session '$SESSION' 已存在，直接 attach${C_RESET}"
  exec tmux attach -t "$SESSION"
fi

# ---- 每个 pane 启动前的公共环境 ----
# 如果项目用 uv，则 VENV 激活可选；直接 uv run 也行
activate_cmd=""
if [[ -f "$VENV" ]]; then
  activate_cmd="source '$VENV' && "
fi

# ---- 检测用 uv 还是 python ----
if command -v uv >/dev/null 2>&1 && [[ -f "$ROOT/pyproject.toml" ]]; then
  RUN_CALC="${activate_cmd}uv run calc_client.py --api-host 0.0.0.0 --pow-difficulty 3 --otlp http://127.0.0.1:4318/v1/traces"
  RUN_APP="${activate_cmd}uv run app.py"
  RUN_SDK="${activate_cmd}uv run sdk.py"
else
  RUN_CALC="${activate_cmd}python3 calc_client.py --api-host 0.0.0.0 --pow-difficulty 3 --otlp http://127.0.0.1:4318/v1/traces"
  RUN_APP="${activate_cmd}python3 app.py"
  RUN_SDK="${activate_cmd}python3 sdk.py"
fi

# ---- 创建 session，第一个 pane 是 calc_client ----
tmux new-session -d -s "$SESSION" -c "$ROOT" -x 200 -y 50

# 给每个 pane 标题栏
tmux set-option -t "$SESSION" pane-border-status top
tmux set-option -t "$SESSION" pane-border-format " #{pane_index} #{pane_title} "

# pane 0：calc_client
tmux select-pane -t "$SESSION:0.0" -T "calc_client"
tmux send-keys -t "$SESSION:0.0" \
  "cd '$ROOT' && $RUN_CALC" C-m

# ---- 垂直分割：左边 calc_client 60%，右边 40% ----
# -h 水平切、-p 给新 pane 的百分比
# 先水平切，让 calc_client 占左边 60%，右边新 pane 占 40%
tmux split-window -h -t "$SESSION:0.0" -p 40 -c "$ROOT"

# ---- 右边的 pane 垂直再切成上下两格 ----
# -v 垂直切，上下各一半
tmux split-window -v -t "$SESSION:0.1" -p 50 -c "$ROOT"

# pane 1：app
tmux select-pane -t "$SESSION:0.1" -T "app"
tmux send-keys -t "$SESSION:0.1" \
  "cd '$ROOT' && $RUN_APP" C-m

# pane 2：sdk
tmux select-pane -t "$SESSION:0.2" -T "sdk"
tmux send-keys -t "$SESSION:0.2" \
  "cd '$ROOT' && $RUN_SDK" C-m

# ---- 焦点回到 calc_client ----
tmux select-pane -t "$SESSION:0.0"

# ---- 状态栏 ----
tmux set-option -t "$SESSION" status-style "bg=#1c2027,fg=#88c0d0"
tmux set-option -t "$SESSION" status-left \
  " #[bold]#{session_name} #[default]| calc-otel "
tmux set-option -t "$SESSION" status-right \
  " #{?pane_in_mode,COPY,} | %H:%M "

# ---- 常用快捷键（可选） ----
# Ctrl+b 是默认前缀，下面加几个：
# Ctrl+b 1/2/3 直接跳 pane
tmux bind-key -T prefix 1 select-pane -t "$SESSION:0.0" 2>/dev/null || true
tmux bind-key -T prefix 2 select-pane -t "$SESSION:0.1" 2>/dev/null || true
tmux bind-key -T prefix 3 select-pane -t "$SESSION:0.2" 2>/dev/null || true

echo -e "${C_TITLE}tmux session 已启动${C_RESET}"
echo ""
echo "  布局:"
echo "    ┌──────────────────────┬───────────┐"
echo "    │                      │  app.py   │"
echo "    │    calc_client.py    ├───────────┤"
echo "    │      (60% 宽)        │  sdk.py   │"
echo "    └──────────────────────┴───────────┘"
echo ""
echo "  快捷键（前缀 Ctrl+b）："
echo "    1 / 2 / 3     跳转到 calc_client / app / sdk"
echo "    z            当前 pane 全屏切换"
echo "    d            detach（进程继续跑）"
echo "    x            关闭当前 pane"
echo "    &            关闭 session"
echo ""
echo -e "  attach: ${C_TITLE}tmux attach -t $SESSION${C_RESET}"
echo -e "  kill:   ${C_TITLE}./dev.sh kill${C_RESET}"
echo ""

# ---- 自动 attach ----
exec tmux attach -t "$SESSION"