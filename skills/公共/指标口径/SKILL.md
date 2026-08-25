---
name: 指标口径
description: 查询销售额、GMV、订单量、退款率等业务指标时，先读这里确认统计口径
---

# 指标口径说明

> 这个文件由业务同学维护。**平台上最值钱的东西就是它**——
> 模型不知道你们公司的 GMV 算不算退款，但业务同学知道。

## GMV

- 口径：`orders` 表中 `status IN ('paid', 'shipped', 'completed')` 的 `amount` 求和
- **必须排除测试订单**：`AND is_test = false`
- 退款不从 GMV 里扣。退款单独看退款率指标。

## 订单量

- 按 `order_id` 去重计数，不要用 `count(*)`——一个订单可能有多条明细行。
- 同样要排除 `is_test = true`。

## 退款率

- 分子：`refunds` 表中 `status = 'success'` 的订单数（按 `order_id` 去重）
- 分母：同期 GMV 口径下的订单量
- 注意分子分母的时间口径要一致，都按**下单时间**而不是退款时间。

## 常见坑

- `orders.created_at` 是 UTC，业务方问「昨天」通常指北京时间，记得 `+8 hours`。
- `amount` 单位是分，不是元。给用户看的时候要除以 100。
