USE chat_module_test;
CREATE TABLE IF NOT EXISTS AllGroup (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    groupname VARCHAR(50) NOT NULL,
    groupdesc VARCHAR(500) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS GroupUser (
    groupid INT NOT NULL,
    userid INT NOT NULL,
    grouprole ENUM('creator','normal') NOT NULL DEFAULT 'normal',
    PRIMARY KEY (groupid,userid),
    CONSTRAINT fk_member_group FOREIGN KEY (groupid) REFERENCES AllGroup(id) ON DELETE CASCADE,
    CONSTRAINT fk_member_user FOREIGN KEY (userid) REFERENCES User(id) ON DELETE CASCADE
) ENGINE=InnoDB;
