#include "model/groupmodel.hpp"
#include "model/usermodel.hpp"
#include "db/db.hpp"
#include <cstdlib>
#include <unistd.h>
#include <iostream>
#include <memory>

int main()
{
    const char* name = std::getenv("CHAT_DB_NAME");
    if (!name || std::string(name) != "chat_module_test") {
        std::cerr << "Use isolated chat_module_test database\n"; return 1;
    }
    User missing; UserModel users;
    if (!users.query(2147483647, missing) || missing.getId() != -1) {
        std::cerr << "Missing-user fixture unavailable\n"; return 1;
    }
    Group group;
    group.name = "rollback_" + std::to_string(getpid());
    group.description = "Test membership failure rolls back group";
    GroupModel model;
    if (model.create(2147483647, group) || group.id != -1 || model.errorCode() != 1452) {
        std::cerr << "Expected missing creator foreign-key failure\n"; return 1;
    }
    MySQL db;
    if (!db.connect()) return 1;
    std::unique_ptr<MYSQL_RES, decltype(&mysql_free_result)> result(
        db.query("SELECT COUNT(*) FROM AllGroup WHERE groupname='" + db.escape(group.name) + "'"), mysql_free_result);
    if (!result) return 1;
    auto row = mysql_fetch_row(result.get());
    if (!row || std::string(row[0]) != "0") {
        std::cerr << "Orphan group remained after failed membership\n"; return 1;
    }
    std::cout << "PASS group insert rolled back when creator membership fails\n";
}
