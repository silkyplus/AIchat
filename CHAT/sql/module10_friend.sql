USE chat_module_test;
CREATE TABLE IF NOT EXISTS Friend (
    userid INT NOT NULL,
    friendid INT NOT NULL,
    PRIMARY KEY (userid,friendid),
    CONSTRAINT fk_friend_user FOREIGN KEY (userid) REFERENCES User(id) ON DELETE CASCADE,
    CONSTRAINT fk_friend_target FOREIGN KEY (friendid) REFERENCES User(id) ON DELETE CASCADE
) ENGINE=InnoDB;
