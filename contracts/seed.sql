-- =============================================================================
-- 初始化数据。容器首次启动时在 schema.sql 之后自动执行。
-- 默认管理员密码为 Admin@123，首次登录后必须修改。
-- =============================================================================

-- 默认组
INSERT INTO sys_group (group_id, name, parent_id, tenant_id) VALUES
  (1, '中控技术',   NULL, 'default'),
  (2, '生产运行部', 1,    'default'),
  (3, '设备管理部', 1,    'default'),
  (4, '质量管理部', 1,    'default'),
  (5, '数据平台组', 1,    'default');
SELECT setval('sys_group_group_id_seq', 5);

-- 默认管理员。password_hash 为 bcrypt('Admin@123')
INSERT INTO sys_user (user_id, username, password_hash, display_name, employee_no, role, tenant_id, created_by)
VALUES (1, 'admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewYFPQ7VNVKLpvGu',
        '系统管理员', 'A0001', 'ADMIN', 'default', 1);
SELECT setval('sys_user_user_id_seq', 1);

INSERT INTO sys_user_group (user_id, group_id) VALUES (1, 5);

-- RBAC 权限点。一期两个角色
INSERT INTO sys_role_permission (role, permission_category, permission_type, permission_subtype) VALUES
  -- 管理员：全部
  ('ADMIN', 'agent',    'read',    NULL),
  ('ADMIN', 'agent',    'write',   NULL),
  ('ADMIN', 'agent',    'delete',  NULL),
  ('ADMIN', 'agent',    'publish', NULL),
  ('ADMIN', 'resource', 'read',    NULL),
  ('ADMIN', 'resource', 'write',   NULL),
  ('ADMIN', 'resource', 'delete',  NULL),
  ('ADMIN', 'record',   'read',    NULL),
  ('ADMIN', 'user',     'read',    NULL),
  ('ADMIN', 'user',     'write',   NULL),
  ('ADMIN', 'user',     'delete',  NULL),
  -- 普通用户：可建自己的智能体、可读资源、可读自己智能体的记录
  ('USER',  'agent',    'read',    NULL),
  ('USER',  'agent',    'write',   NULL),
  ('USER',  'agent',    'delete',  NULL),
  ('USER',  'agent',    'publish', NULL),
  ('USER',  'resource', 'read',    NULL),
  ('USER',  'resource', 'write',   'mcp'),
  ('USER',  'resource', 'write',   'skill'),
  ('USER',  'record',   'read',    NULL);

-- 注意：RBAC 只管「这类操作能不能做」，具体某个资源能不能碰由 DAC 判定
-- （created_by + group_ids + ingroup_permission），见 contracts/README.md
