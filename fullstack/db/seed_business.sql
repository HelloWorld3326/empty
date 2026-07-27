-- =====================================================================
--  业务表样例数据 —— 这一层假装是"源系统同步过来的"
--
--  注意几个刻意的脏点，映射层要负责把它们变成人话：
--    · member_lvl 存码值 01/02/03
--    · unit_price / subtotal 以分为单位
--    · 列名是缩写：full_name / prod_name / ord_status / carrier_cd
-- =====================================================================

INSERT INTO code_member_level (code, label) VALUES
  ('01', '普通'),
  ('02', '银卡'),
  ('03', '金卡');

-- 买家：真实系统里这些由数据管道同步进来，不需要 Action 创建
INSERT INTO buyer (buyer_id, full_name, phone_no, member_lvl, city_name) VALUES
  ('B-1', '周韫',   '138****2210', '03', '上海'),
  ('B-2', '林澈',   '139****0455', '01', '成都'),
  ('B-3', '苏雁',   '137****8801', '02', '杭州'),
  ('B-4', '贺听寒', '186****3372', '01', '广州');

-- 商品：单价以分存储
INSERT INTO product (sku, prod_name, unit_price, stock_qty, row_version) VALUES
  ('AUD-Q30', '降噪耳机 Q30',    89900, 40,  1),
  ('KBD-K7',  '机械键盘 K7',     45900, 25,  1),
  ('HUB-C8',  'USB-C 扩展坞',    19900, 120, 1),
  ('CHR-E5',  '人体工学椅 E5',  189900, 12,  1),
  ('MON-U27', '显示器 U27 4K',  229900, 8,   1);

-- 两张历史订单，让你一进来就有东西可看可点
INSERT INTO orders (order_no, buyer_id, ord_status, placed_at, ship_to_city, carrier_cd, row_version) VALUES
  ('O-1001', 'B-1', '已完成', '2026-07-20 10:12', '上海', '顺丰',   1),
  ('O-1002', 'B-3', '待支付', '2026-07-26 16:40', '杭州', NULL,     1);

INSERT INTO order_line (line_no, order_no, sku, qty, unit_price, subtotal, row_version) VALUES
  ('L-1', 'O-1001', 'AUD-Q30', 2, 89900, 179800, 1),
  ('L-2', 'O-1001', 'HUB-C8',  1, 19900,  19900, 1),
  ('L-3', 'O-1002', 'KBD-K7',  1, 45900,  45900, 1);

-- 上面两张订单已经占用过库存，这里把账做平：
--   AUD-Q30 40-2=38   HUB-C8 120-1=119   KBD-K7 25-1=24
UPDATE product SET stock_qty = 38  WHERE sku = 'AUD-Q30';
UPDATE product SET stock_qty = 119 WHERE sku = 'HUB-C8';
UPDATE product SET stock_qty = 24  WHERE sku = 'KBD-K7';
