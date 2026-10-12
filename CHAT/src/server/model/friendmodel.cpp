#include "model/friendmodel.hpp"
#include "db/db.hpp"
#include <algorithm>
#include <memory>
#include <cstdlib>

AddFriendResult FriendModel::add(int userId, int friendId)
{
    errorCode_ = 0;
    if (userId <= 0 || friendId <= 0 || userId == friendId) return AddFriendResult::Invalid;
    // 双向请求采用相同的主键插入顺序，降低并发反向添加的死锁概率。
    const int a = std::min(userId, friendId), b = std::max(userId, friendId);
    for (int attempt = 0; attempt < 3; ++attempt) {
        MySQL db;
        if (!db.connect() || !db.update("START TRANSACTION")) {
            errorCode_ = db.errorCode(); return AddFriendResult::DatabaseError;
        }
        const std::string sql = "INSERT INTO Friend(userid,friendid) VALUES (" +
            std::to_string(a) + "," + std::to_string(b) + "),(" +
            std::to_string(b) + "," + std::to_string(a) + ")";
        if (db.update(sql)) {
            if (db.update("COMMIT")) return AddFriendResult::Success;
            errorCode_ = db.errorCode(); db.update("ROLLBACK");
            return AddFriendResult::DatabaseError;
        }
        errorCode_ = db.errorCode();
        db.update("ROLLBACK");
        if (errorCode_ == 1062) return AddFriendResult::Duplicate;
        if (errorCode_ == 1452) return AddFriendResult::NotFound;
        if (errorCode_ != 1213 && errorCode_ != 1205) return AddFriendResult::DatabaseError;
    }
    return AddFriendResult::DatabaseError;
}

bool FriendModel::query(int userId, std::vector<FriendInfo>& friends)
{
    friends.clear(); errorCode_ = 0;
    MySQL db;
    if (!db.connect()) { errorCode_ = db.errorCode(); return false; }
    std::unique_ptr<MYSQL_RES, decltype(&mysql_free_result)> result(
        db.query("SELECT u.id,u.name,u.state FROM Friend f JOIN User u ON u.id=f.friendid "
            "WHERE f.userid=" + std::to_string(userId) + " ORDER BY u.id"), mysql_free_result);
    if (!result) { errorCode_ = db.errorCode(); return false; }
    while (MYSQL_ROW row = mysql_fetch_row(result.get()))
        friends.push_back(FriendInfo{std::atoi(row[0]),row[1],row[2]});
    return true;
}
