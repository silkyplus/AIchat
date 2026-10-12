-- 对已有测试库执行一次；应用不自动修改表结构。
USE chat_module_test;
ALTER TABLE User ADD COLUMN server_id VARCHAR(64) NULL DEFAULT NULL,
    ADD INDEX idx_user_server (server_id);
-- 不自动修改无节点归属的历史在线账号。
