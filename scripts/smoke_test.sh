#!/usr/bin/env bash
# 工具 API 冒烟测试：验证「绑定后能查自己的数据、查不到别人的数据」。
set -euo pipefail

BASE="${TOOL_API_BASE_URL:-http://127.0.0.1:8100}"
KEY="${TOOL_API_SERVICE_KEY:?请先 export TOOL_API_SERVICE_KEY}"
CHAT_A="chat-aaa"
CHAT_B="chat-bbb"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1. 健康检查"
curl -sf "$BASE/healthz"; echo

say "2. 把两个会话分别绑定到两个客户"
curl -sf -X POST "$BASE/internal/bind" -H "X-Service-Key: $KEY" -H 'Content-Type: application/json' \
  -d "{\"chat_id\":\"$CHAT_A\",\"customer_id\":\"C10001\"}"; echo
curl -sf -X POST "$BASE/internal/bind" -H "X-Service-Key: $KEY" -H 'Content-Type: application/json' \
  -d "{\"chat_id\":\"$CHAT_B\",\"customer_id\":\"C10002\"}"; echo

say "3. 会话A 查自己的订单（应看到退款中的订单，手机号已脱敏）"
curl -sf "$BASE/tools/customer/orders" -H "X-Service-Key: $KEY" -H "X-Chat-Id: $CHAT_A"; echo

say "4. 会话B 查会话A 的订单号（应 404，这是防越权的关键）"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/tools/customer/orders/SO2026080100123" \
  -H "X-Service-Key: $KEY" -H "X-Chat-Id: $CHAT_B")
[ "$code" = "404" ] && echo "OK：返回 $code，查不到别人的订单" || { echo "危险：返回 $code"; exit 1; }

say "5. 不带 Service Key（应 401）"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/tools/customer/profile" -H "X-Chat-Id: $CHAT_A")
[ "$code" = "401" ] && echo "OK：返回 $code" || { echo "危险：返回 $code"; exit 1; }

say "6. 未绑定的会话（应 403）"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/tools/customer/profile" \
  -H "X-Service-Key: $KEY" -H "X-Chat-Id: chat-not-bound")
[ "$code" = "403" ] && echo "OK：返回 $code" || { echo "危险：返回 $code"; exit 1; }

say "全部通过 ✅"
