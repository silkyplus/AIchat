#include "model/usermodel.hpp"
#include "db/db.hpp"
#include "node.hpp"
#include <memory>
#include <cstdlib>

bool UserModel::insert(User& user) {
    errorCode_ = 0;
    MySQL db;
    if (!db.connect()) { error_ = db.error(); errorCode_ = db.errorCode(); return false; }
    std::string sql = "INSERT INTO User(name,password,state) VALUES ('" +
        db.escape(user.getName()) + "','" + db.escape(user.getPassword()) + "','offline')";
    if (!db.update(sql)) { error_ = db.error(); errorCode_ = db.errorCode(); return false; }
    user.setId(static_cast<int>(db.insertId()));
    user.setState("offline"); error_.clear(); return true;
}
bool UserModel::query(int id, User& user) {
    user = User{};
    errorCode_ = 0;
    MySQL db;
    if (!db.connect()) { error_ = db.error(); errorCode_ = db.errorCode(); return false; }
    std::unique_ptr<MYSQL_RES, decltype(&mysql_free_result)> result(
        db.query("SELECT id,name,password,state FROM User WHERE id=" + std::to_string(id)), mysql_free_result);
    if (!result) { error_ = db.error(); errorCode_ = db.errorCode(); return false; }
    MYSQL_ROW row = mysql_fetch_row(result.get());
    if (row) {
        user.setId(std::atoi(row[0])); user.setName(row[1]);
        user.setPassword(row[2]); user.setState(row[3]);
    }
    error_.clear(); return true;
}
bool UserModel::updateState(const User& user) {
    if (user.getState() != "online" && user.getState() != "offline") {
        error_ = "invalid user state"; return false;
    }
    errorCode_ = 0;
    MySQL db;
    if (!db.connect()) { error_ = db.error(); errorCode_ = db.errorCode(); return false; }
    const std::string node = db.escape(currentNodeId());
    const std::string assignment = user.getState() == "online"
        ? "state='online',server_id='" + node + "'"
        : "state='offline',server_id=NULL";
    bool ok = db.update("UPDATE User SET " + assignment + " WHERE id=" +
        std::to_string(user.getId()) + " AND (server_id='" + node + "' OR server_id IS NULL)");
    error_ = db.error(); errorCode_ = db.errorCode(); return ok;
}

bool UserModel::trySetOnline(int id, bool& acquired) {
    acquired = false; errorCode_ = 0; error_.clear();
    MySQL db;
    if (!db.connect() || !db.update("UPDATE User SET state='online',server_id='" + db.escape(currentNodeId()) +
        "' WHERE id=" + std::to_string(id) + " AND state='offline'")) {
        error_ = db.error(); errorCode_ = db.errorCode(); return false;
    }
    acquired = db.affectedRows() == 1;
    return true;
}

bool UserModel::resetStateForServer() {
    errorCode_ = 0; error_.clear();
    MySQL db;
    if (!db.connect() || !db.update("UPDATE User SET state='offline',server_id=NULL WHERE server_id='" +
        db.escape(currentNodeId()) + "'")) {
        error_ = db.error(); errorCode_ = db.errorCode(); return false;
    }
    return true;
}
