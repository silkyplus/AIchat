-- 在课程测试库中增量创建离线消息表，不删除已有数据。
USE chat_module_test;
CREATE TABLE IF NOT EXISTS OfflineMessage (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    userid INT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_recipient_id (userid, id),
    CONSTRAINT fk_offline_user FOREIGN KEY (userid) REFERENCES User(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
