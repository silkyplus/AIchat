-- 仅创建独立模块测试库，不修改已有项目数据库。
CREATE DATABASE IF NOT EXISTS chat_module_test CHARACTER SET utf8mb4;
USE chat_module_test;
CREATE TABLE IF NOT EXISTS User (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL COMMENT 'Password hash; never plaintext',
    state ENUM('online','offline') NOT NULL DEFAULT 'offline'
) ENGINE=InnoDB;
-- 账号密码使用独立测试值，后续业务库另行配置。
CREATE USER IF NOT EXISTS 'chat_module_test'@'127.0.0.1' IDENTIFIED BY 'Module02_test_only!';
GRANT SELECT, INSERT, UPDATE, DELETE ON chat_module_test.* TO 'chat_module_test'@'127.0.0.1';
