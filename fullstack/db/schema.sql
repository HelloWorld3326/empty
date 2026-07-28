-- =====================================================================
--  Ontology Lab —— 三层建表
--
--  第 1 层  业务表      源系统长什么样就什么样：缩写列名、码值、分为单位
--  第 2 层  本体元数据  对象类型/属性/链接/操作，以及它们到业务表的映射
--  第 3 层  审计        每一次 Action 提交都留痕
--
--  故意把业务表写"脏"，映射层才有活干 —— 那正是本体存在的意义。
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
--  第 1 层：业务表
-- ---------------------------------------------------------------------

-- 码表。真实系统里到处都是这种东西：库里存 '02'，人要看的是"银卡"。
CREATE TABLE code_member_level (
  code   TEXT PRIMARY KEY,
  label  TEXT NOT NULL
);

CREATE TABLE buyer (
  buyer_id    TEXT PRIMARY KEY,
  full_name   TEXT NOT NULL,
  phone_no    TEXT,
  member_lvl  TEXT REFERENCES code_member_level(code),
  city_name   TEXT
);

CREATE TABLE product (
  sku          TEXT PRIMARY KEY,
  prod_name    TEXT NOT NULL,
  unit_price   INTEGER NOT NULL DEFAULT 0,   -- 分
  stock_qty    INTEGER NOT NULL DEFAULT 0,
  row_version  INTEGER NOT NULL DEFAULT 1    -- 乐观锁
);

CREATE TABLE orders (
  order_no      TEXT PRIMARY KEY,
  buyer_id      TEXT REFERENCES buyer(buyer_id),   -- 外键 → 会被映射成 placed 链接
  ord_status    TEXT NOT NULL DEFAULT '待支付',
  placed_at     TEXT,
  ship_to_city  TEXT,
  carrier_cd    TEXT,
  row_version   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE order_line (
  line_no      TEXT PRIMARY KEY,
  order_no     TEXT REFERENCES orders(order_no),   -- 外键 → hasLine 链接
  sku          TEXT REFERENCES product(sku),       -- 外键 → ofProduct 链接
  qty          INTEGER NOT NULL DEFAULT 0,
  unit_price   INTEGER NOT NULL DEFAULT 0,         -- 分。成交价要冻结，不能跟着商品涨跌
  subtotal     INTEGER NOT NULL DEFAULT 0,         -- 分
  row_version  INTEGER NOT NULL DEFAULT 1
);

-- 这张表**故意不在初始本体里**。
-- 它是留给你练手的：把它绑成一个对象类型，看外键列怎么被自动跳过，
-- 再把那个外键建成链接类型。三种类型的创建路径走一遍就全懂了。
CREATE TABLE shipment (
  tracking_no  TEXT PRIMARY KEY,
  order_no     TEXT REFERENCES orders(order_no),   -- 外键：该被建成链接，不是属性
  carrier_cd   TEXT,
  ship_status  TEXT,
  eta          TEXT,
  row_version  INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_orders_buyer     ON orders(buyer_id);
CREATE INDEX idx_line_order       ON order_line(order_no);
CREATE INDEX idx_line_sku         ON order_line(sku);
CREATE INDEX idx_shipment_order   ON shipment(order_no);

-- ---------------------------------------------------------------------
--  第 2 层：本体元数据（含到业务表的映射）
-- ---------------------------------------------------------------------

-- 对象类型必须绑定一张真实的表。凭空造不出对象类型 —— 这是和内存版最大的区别。
CREATE TABLE object_type (
  api_name      TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  source_table  TEXT NOT NULL,      -- 映射：绑定哪张业务表
  pk_column     TEXT NOT NULL,      -- 映射：哪一列是主键
  title_prop    TEXT,               -- 列表和图谱里显示哪个属性
  color         INTEGER NOT NULL DEFAULT 0,
  id_prefix     TEXT,               -- Action 新建对象时生成主键用（业务自带主键的类型可为空）
  next_seq      INTEGER NOT NULL DEFAULT 1,   -- 主键序号，在事务里自增
  sort_order    INTEGER NOT NULL DEFAULT 0
);

-- 属性不是凭空加的，是映射一个已存在的列。列不存在就得先改表。
CREATE TABLE object_property (
  object_type    TEXT NOT NULL REFERENCES object_type(api_name) ON DELETE CASCADE,
  api_name       TEXT NOT NULL,
  display_name   TEXT NOT NULL,
  data_type      TEXT NOT NULL,     -- string/integer/currency/enum/timestamp/date/boolean
  source_column  TEXT NOT NULL,     -- 映射：来自哪一列
  value_map      TEXT,              -- JSON：码值 → 显示值，如 {"01":"普通"}
  options        TEXT,              -- JSON 数组：枚举的取值范围
  scale          INTEGER,           -- currency 用：库里存分，读时除以 100，写时乘回去
  editable       INTEGER NOT NULL DEFAULT 1,
  sort_order     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (object_type, api_name)
);

-- 链接由一个真实外键实现。
--   fk_table 落在 to 端  → 从 A 出发：SELECT * FROM to_table WHERE fk_col = A.pk
--   fk_table 落在 from 端 → 从 A 出发：SELECT * FROM to_table WHERE to_pk  = A.fk_col
-- 链接方向和外键方向是两件独立的事，这两行代码就是证据。
CREATE TABLE link_type (
  api_name     TEXT PRIMARY KEY,
  from_type    TEXT NOT NULL REFERENCES object_type(api_name) ON DELETE CASCADE,
  to_type      TEXT NOT NULL REFERENCES object_type(api_name) ON DELETE CASCADE,
  cardinality  TEXT NOT NULL,       -- '1 : 1' / '1 : N' / 'N : 1' / 'N : N'
  fwd_label    TEXT NOT NULL,
  inv_label    TEXT NOT NULL,
  join_kind    TEXT NOT NULL DEFAULT 'fk',
  fk_table     TEXT NOT NULL,
  fk_column    TEXT NOT NULL,
  sort_order   INTEGER NOT NULL DEFAULT 0
);

-- 操作类型：参数 / 校验规则 / 写入编辑，全是声明式 JSON，由服务端解释器求值。
CREATE TABLE action_type (
  api_name       TEXT PRIMARY KEY,
  display_name   TEXT NOT NULL,
  description    TEXT,
  params         TEXT NOT NULL DEFAULT '[]',
  rules          TEXT NOT NULL DEFAULT '[]',
  edits          TEXT NOT NULL DEFAULT '[]',
  required_role  TEXT,              -- NULL = 谁都能跑
  sort_order     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE app_user (
  username      TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  role          TEXT NOT NULL
);

-- ---------------------------------------------------------------------
--  第 3 层：审计
-- ---------------------------------------------------------------------

-- 没有通用撤销。已经发生的事实不该被抹掉 —— 要回退就跑补偿性 Action
-- （取消订单、删除订单行），那也是一条新的记录。
CREATE TABLE action_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  actor        TEXT NOT NULL,
  action_type  TEXT NOT NULL,
  params       TEXT NOT NULL DEFAULT '{}',
  edits        TEXT NOT NULL DEFAULT '[]',
  status       TEXT NOT NULL,       -- applied / rejected
  error        TEXT
);

CREATE INDEX idx_log_ts ON action_log(id DESC);

-- 本体自己的变更也要留痕：schema 也是数据。
CREATE TABLE schema_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT NOT NULL,
  actor   TEXT NOT NULL,
  summary TEXT NOT NULL
);
