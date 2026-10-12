#include "model/usermodel.hpp"
#include "db/db.hpp"
#include <iostream>
#include <unistd.h>
#include <stdexcept>

int main() {
    if (!std::getenv("CHAT_DB_NAME") || std::string(std::getenv("CHAT_DB_NAME")) != "chat_module_test") {
        std::cerr << "Set CHAT_DB_NAME=chat_module_test (isolated test database).\n"; return 1;
    }
    int id = -1;
    try {
        UserModel model;
        auto check = [&](bool ok) { if (!ok) throw std::runtime_error(model.error()); };
        User user;
        user.setName("module02_中文'O" + std::to_string(getpid()));
        user.setPassword("test_hash_placeholder");
        check(model.insert(user)); id = user.getId();
        User loaded;
        check(model.query(id, loaded));
        if (loaded.getId() != id || loaded.getName() != user.getName() || loaded.getState() != "offline")
            throw std::runtime_error("insert/query mismatch");
        std::cout << "PASS insert/query (UTF-8 and quote escaping)\n";
        user.setState("online"); check(model.updateState(user)); check(model.query(id, loaded));
        if (loaded.getState() != "online") throw std::runtime_error("state mismatch");
        std::cout << "PASS update state\n";
        check(model.query(-1, loaded));
        if (loaded.getId() != -1) throw std::runtime_error("missing-user mismatch");
        std::cout << "PASS missing user\n";
        MySQL db;
        if (!db.connect() || !db.update("DELETE FROM User WHERE id=" + std::to_string(id)))
            throw std::runtime_error("cleanup: " + db.error());
        std::cout << "PASS test row removed\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: " << e.what() << " (test row id=" << id << ")\n"; return 1;
    }
}
