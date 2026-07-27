-- =====================================================================
--  本体定义 —— 把上一层的业务表映射成对象、链接、操作
--
--  看这份文件时请注意两件事：
--
--  1. orders.buyer_id / order_line.order_no / order_line.sku 这三个外键列
--     **没有**出现在 object_property 里。它们不是属性，是链接。
--     这是关系模型到本体最核心的一次转换。
--
--  2. buyer 没有"新建买家"操作。买家由数据管道同步进来 ——
--     只有真正的业务写入才需要 Action。
-- =====================================================================

-- ---------------------------------------------------------------------
--  对象类型：每一个都必须绑定一张真实的表
-- ---------------------------------------------------------------------
INSERT INTO object_type
  (api_name, display_name, source_table, pk_column, title_prop, color, id_prefix, next_seq, sort_order) VALUES
  ('Buyer',     '买家',   'buyer',      'buyer_id', 'name',    0, NULL, 1,    1),
  ('Product',   '商品',   'product',    'sku',      'name',    2, NULL, 1,    2),
  ('Order',     '订单',   'orders',     'order_no', 'orderNo', 1, 'O',  1003, 3),
  ('OrderLine', '订单行', 'order_line', 'line_no',  'lineNo',  5, 'L',  4,    4);

-- ---------------------------------------------------------------------
--  属性：每一个都映射到一个已存在的列
-- ---------------------------------------------------------------------

-- Buyer：member_lvl 存码值，靠 value_map 变成人话
INSERT INTO object_property
  (object_type, api_name, display_name, data_type, source_column, value_map, options, scale, editable, sort_order) VALUES
  ('Buyer', 'buyerId', '买家编号', 'string', 'buyer_id',  NULL, NULL, NULL, 0, 1),
  ('Buyer', 'name',    '姓名',     'string', 'full_name', NULL, NULL, NULL, 1, 2),
  ('Buyer', 'phone',   '手机',     'string', 'phone_no',  NULL, NULL, NULL, 1, 3),
  ('Buyer', 'level',   '会员等级', 'enum',   'member_lvl',
   '{"01":"普通","02":"银卡","03":"金卡"}', '["普通","银卡","金卡"]', NULL, 1, 4),
  ('Buyer', 'city',    '城市',     'string', 'city_name', NULL, NULL, NULL, 1, 5);

-- Product：unit_price 库里是分，scale=100 让它以元显示
INSERT INTO object_property
  (object_type, api_name, display_name, data_type, source_column, value_map, options, scale, editable, sort_order) VALUES
  ('Product', 'sku',   'SKU',    'string',   'sku',        NULL, NULL, NULL, 0, 1),
  ('Product', 'name',  '商品名', 'string',   'prod_name',  NULL, NULL, NULL, 1, 2),
  ('Product', 'price', '单价',   'currency', 'unit_price', NULL, NULL, 100,  1, 3),
  ('Product', 'stock', '库存',   'integer',  'stock_qty',  NULL, NULL, NULL, 1, 4);

-- Order：注意这里没有 buyer_id —— 它是 placed 链接，不是属性
INSERT INTO object_property
  (object_type, api_name, display_name, data_type, source_column, value_map, options, scale, editable, sort_order) VALUES
  ('Order', 'orderNo',  '订单号',   'string',    'order_no',     NULL, NULL, NULL, 0, 1),
  ('Order', 'status',   '状态',     'enum',      'ord_status',   NULL,
   '["待支付","已支付","已发货","已完成","已取消"]', NULL, 1, 2),
  ('Order', 'placedAt', '下单时间', 'timestamp', 'placed_at',    NULL, NULL, NULL, 1, 3),
  ('Order', 'shipTo',   '收货城市', 'string',    'ship_to_city', NULL, NULL, NULL, 1, 4),
  ('Order', 'carrier',  '承运商',   'string',    'carrier_cd',   NULL, NULL, NULL, 1, 5);

-- OrderLine：同样没有 order_no / sku，它们是 hasLine 和 ofProduct
INSERT INTO object_property
  (object_type, api_name, display_name, data_type, source_column, value_map, options, scale, editable, sort_order) VALUES
  ('OrderLine', 'lineNo',    '行号',     'string',   'line_no',    NULL, NULL, NULL, 0, 1),
  ('OrderLine', 'qty',       '数量',     'integer',  'qty',        NULL, NULL, NULL, 1, 2),
  ('OrderLine', 'unitPrice', '成交单价', 'currency', 'unit_price', NULL, NULL, 100,  1, 3),
  ('OrderLine', 'subtotal',  '小计',     'currency', 'subtotal',   NULL, NULL, 100,  1, 4);

-- ---------------------------------------------------------------------
--  链接：每一条由一个真实外键实现
--
--  placed / hasLine 的外键在 to 端，ofProduct 的外键在 from 端 ——
--  链接方向和外键方向是两件独立的事。
-- ---------------------------------------------------------------------
INSERT INTO link_type
  (api_name, from_type, to_type, cardinality, fwd_label, inv_label, join_kind, fk_table, fk_column, sort_order) VALUES
  ('placed',    'Buyer',     'Order',     '1 : N', '下的订单',   '下单买家',   'fk', 'orders',     'buyer_id', 1),
  ('hasLine',   'Order',     'OrderLine', '1 : N', '订单行',     '所属订单',   'fk', 'order_line', 'order_no', 2),
  ('ofProduct', 'OrderLine', 'Product',   'N : 1', '对应商品',   '相关订单行', 'fk', 'order_line', 'sku',      3);

-- ---------------------------------------------------------------------
--  用户与角色
-- ---------------------------------------------------------------------
INSERT INTO app_user (username, display_name, role) VALUES
  ('xiaozhang', '小张', '客服'),
  ('laowang',   '老王', '仓管'),
  ('admin',     '管理员', '管理员');

-- ---------------------------------------------------------------------
--  操作类型
--
--  参数 / 校验 / 编辑全是声明式 JSON，由 server/actions.py 的解释器求值。
--  "建立链接" 落到 SQL 就是写外键列 —— 所以基数天然被表结构约束住：
--  orders.buyer_id 只能装一个值，一张订单就只能有一个买家。
-- ---------------------------------------------------------------------

INSERT INTO action_type (api_name, display_name, description, required_role, sort_order, params, rules, edits) VALUES
('createOrder', '下单',
 '开一张空订单并挂到买家名下。此时还没有商品 —— 商品是下一步一行一行加进去的。',
 '客服', 1,
 '[{"id":"buyer","cn":"买家","kind":"object","objectType":"Buyer"},
   {"id":"shipTo","cn":"收货城市","kind":"text","def":""}]',
 '[]',
 '[{"uid":"e1","op":"create","objectType":"Order","props":{
      "status":{"src":"const","value":"待支付"},
      "placedAt":{"src":"now"},
      "shipTo":{"src":"param","param":"shipTo"}}},
   {"uid":"e2","op":"link","link":"placed",
      "a":{"kind":"param","param":"buyer"},"b":{"kind":"new","uid":"e1"}}]'),

('addLine', '加购商品',
 '新建一个订单行，同时挂到订单和商品上，并扣减库存。数量和成交单价属于这一行，既不属于订单也不属于商品。',
 '客服', 2,
 '[{"id":"order","cn":"订单","kind":"object","objectType":"Order"},
   {"id":"product","cn":"商品","kind":"object","objectType":"Product"},
   {"id":"qty","cn":"数量","kind":"number","def":1}]',
 '[{"type":"compare","left":{"src":"prop","of":{"kind":"param","param":"order"},"prop":"status"},
    "op":"==","right":{"src":"const","value":"待支付"}},
   {"type":"compare","left":{"src":"param","param":"qty"},"op":">=","right":{"src":"const","value":1}},
   {"type":"compare","left":{"src":"prop","of":{"kind":"param","param":"product"},"prop":"stock"},
    "op":">=","right":{"src":"param","param":"qty"}}]',
 '[{"uid":"e1","op":"create","objectType":"OrderLine","props":{
      "qty":{"src":"param","param":"qty"},
      "unitPrice":{"src":"prop","of":{"kind":"param","param":"product"},"prop":"price"},
      "subtotal":{"src":"calc","a":{"src":"prop","of":{"kind":"param","param":"product"},"prop":"price"},
                  "op":"*","b":{"src":"param","param":"qty"}}}},
   {"uid":"e2","op":"link","link":"hasLine",
      "a":{"kind":"param","param":"order"},"b":{"kind":"new","uid":"e1"}},
   {"uid":"e3","op":"link","link":"ofProduct",
      "a":{"kind":"new","uid":"e1"},"b":{"kind":"param","param":"product"}},
   {"uid":"e4","op":"modify","target":{"kind":"param","param":"product"},
      "prop":"stock","mode":"dec","value":{"src":"param","param":"qty"}}]'),

('removeLine', '删除订单行',
 '参数只给了订单行，规则却要看它所属订单的状态，编辑还要把库存还给它对应的商品 —— 两次链接遍历，方向相反。',
 '客服', 3,
 '[{"id":"line","cn":"订单行","kind":"object","objectType":"OrderLine"}]',
 '[{"type":"compare",
    "left":{"src":"prop","of":{"kind":"hop","from":{"kind":"param","param":"line"},
            "link":"hasLine","dir":"inv"},"prop":"status"},
    "op":"==","right":{"src":"const","value":"待支付"}}]',
 '[{"uid":"e1","op":"modify",
      "target":{"kind":"hop","from":{"kind":"param","param":"line"},"link":"ofProduct","dir":"fwd"},
      "prop":"stock","mode":"inc",
      "value":{"src":"prop","of":{"kind":"param","param":"line"},"prop":"qty"}},
   {"uid":"e2","op":"delete","target":{"kind":"param","param":"line"}}]'),

('payOrder', '支付',
 '空订单不能支付 —— 用「链接数量」规则守住：订单行至少得有一条。',
 '客服', 4,
 '[{"id":"order","cn":"订单","kind":"object","objectType":"Order"}]',
 '[{"type":"compare","left":{"src":"prop","of":{"kind":"param","param":"order"},"prop":"status"},
    "op":"==","right":{"src":"const","value":"待支付"}},
   {"type":"linkCount","target":{"kind":"param","param":"order"},
    "link":"hasLine","dir":"fwd","op":">=","n":1}]',
 '[{"uid":"e1","op":"modify","target":{"kind":"param","param":"order"},
      "prop":"status","mode":"set","value":{"src":"const","value":"已支付"}}]'),

('shipOrder', '发货',
 '仓管才能发货。两个人同时点这个按钮，只有一个能成功 —— 另一个会撞上乐观锁。',
 '仓管', 5,
 '[{"id":"order","cn":"订单","kind":"object","objectType":"Order"},
   {"id":"carrier","cn":"承运商","kind":"enum","options":["顺丰","中通","京东物流"],"def":"顺丰"}]',
 '[{"type":"compare","left":{"src":"prop","of":{"kind":"param","param":"order"},"prop":"status"},
    "op":"==","right":{"src":"const","value":"已支付"}}]',
 '[{"uid":"e1","op":"modify","target":{"kind":"param","param":"order"},
      "prop":"carrier","mode":"set","value":{"src":"param","param":"carrier"}},
   {"uid":"e2","op":"modify","target":{"kind":"param","param":"order"},
      "prop":"status","mode":"set","value":{"src":"const","value":"已发货"}}]'),

('completeOrder', '确认收货',
 '状态机的最后一步。',
 '仓管', 6,
 '[{"id":"order","cn":"订单","kind":"object","objectType":"Order"}]',
 '[{"type":"compare","left":{"src":"prop","of":{"kind":"param","param":"order"},"prop":"status"},
    "op":"==","right":{"src":"const","value":"已发货"}}]',
 '[{"uid":"e1","op":"modify","target":{"kind":"param","param":"order"},
      "prop":"status","mode":"set","value":{"src":"const","value":"已完成"}}]'),

('cancelOrder', '取消订单',
 '只改状态，不退库存 —— 退库存要一行一行走「删除订单行」。这是简化模型的边界。',
 '客服', 7,
 '[{"id":"order","cn":"订单","kind":"object","objectType":"Order"},
   {"id":"reason","cn":"原因","kind":"enum","options":["买家取消","缺货","地址有误"],"def":"买家取消"}]',
 '[{"type":"compare","left":{"src":"prop","of":{"kind":"param","param":"order"},"prop":"status"},
    "op":"in","right":{"src":"consts","values":["待支付","已支付"]}}]',
 '[{"uid":"e1","op":"modify","target":{"kind":"param","param":"order"},
      "prop":"status","mode":"set","value":{"src":"const","value":"已取消"}}]'),

('restock', '补货',
 '仓管给商品加库存。',
 '仓管', 8,
 '[{"id":"product","cn":"商品","kind":"object","objectType":"Product"},
   {"id":"qty","cn":"数量","kind":"number","def":10}]',
 '[{"type":"compare","left":{"src":"param","param":"qty"},"op":">=","right":{"src":"const","value":1}}]',
 '[{"uid":"e1","op":"modify","target":{"kind":"param","param":"product"},
      "prop":"stock","mode":"inc","value":{"src":"param","param":"qty"}}]'),

('createProduct', '上架商品',
 'SKU 由业务给定，不是系统生成的 —— 所以这条 create 编辑显式指定了主键。',
 '管理员', 9,
 '[{"id":"sku","cn":"SKU","kind":"text","def":""},
   {"id":"name","cn":"商品名","kind":"text","def":""},
   {"id":"price","cn":"单价（元）","kind":"number","def":0},
   {"id":"stock","cn":"初始库存","kind":"number","def":0}]',
 '[{"type":"compare","left":{"src":"param","param":"price"},"op":">=","right":{"src":"const","value":0}},
   {"type":"compare","left":{"src":"param","param":"stock"},"op":">=","right":{"src":"const","value":0}}]',
 '[{"uid":"e1","op":"create","objectType":"Product",
      "pk":{"src":"param","param":"sku"},
      "props":{"name":{"src":"param","param":"name"},
               "price":{"src":"param","param":"price"},
               "stock":{"src":"param","param":"stock"}}}]');
