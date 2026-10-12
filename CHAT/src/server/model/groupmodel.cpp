#include "model/groupmodel.hpp"
#include "db/db.hpp"
#include <limits>
#include <memory>
#include <cstdlib>

bool GroupModel::create(int creatorId, Group& group)
{
    group.id = -1; errorCode_ = 0;
    if (creatorId <= 0 || group.name.empty() || group.name.size() > 50 ||
        group.description.size() > 500 || group.name.find('\0') != std::string::npos ||
        group.description.find('\0') != std::string::npos) return false;
    MySQL db;
    if (!db.connect() || !db.update("START TRANSACTION")) {
        errorCode_ = db.errorCode(); return false;
    }
    try {
        if (!db.update("INSERT INTO AllGroup(groupname,groupdesc) VALUES ('" +
            db.escape(group.name) + "','" + db.escape(group.description) + "')")) {
            errorCode_ = db.errorCode(); db.update("ROLLBACK"); return false;
        }
        const auto id = db.insertId();
        if (id > static_cast<unsigned long long>(std::numeric_limits<int>::max())) {
            db.update("ROLLBACK"); return false;
        }
        if (!db.update("INSERT INTO GroupUser(groupid,userid,grouprole) VALUES (" +
            std::to_string(id) + "," + std::to_string(creatorId) + ",'creator')") ||
            !db.update("COMMIT")) {
            errorCode_ = db.errorCode(); db.update("ROLLBACK"); return false;
        }
        group.id = static_cast<int>(id);
        return true;
    } catch (...) {
        db.update("ROLLBACK"); throw;
    }
}

JoinGroupResult GroupModel::join(int userId, int groupId)
{
    errorCode_ = 0;
    if (userId <= 0 || groupId <= 0) return JoinGroupResult::Invalid;
    MySQL db;
    if (!db.connect()) { errorCode_ = db.errorCode(); return JoinGroupResult::DatabaseError; }
    // INSERT SELECT：群不存在时影响0行；所有新加入者都是normal，不能指定群主。
    if (!db.update("INSERT INTO GroupUser(groupid,userid,grouprole) SELECT id," +
        std::to_string(userId) + ",'normal' FROM AllGroup WHERE id=" + std::to_string(groupId))) {
        errorCode_ = db.errorCode();
        if (errorCode_ == 1062) return JoinGroupResult::Duplicate;
        if (errorCode_ == 1452) return JoinGroupResult::NotFound;
        return JoinGroupResult::DatabaseError;
    }
    return db.affectedRows() == 1 ? JoinGroupResult::Success : JoinGroupResult::NotFound;
}

bool GroupModel::query(int userId, std::vector<Group>& groups)
{
    groups.clear(); errorCode_ = 0;
    MySQL db;
    if (!db.connect()) { errorCode_ = db.errorCode(); return false; }
    std::unique_ptr<MYSQL_RES, decltype(&mysql_free_result)> result(
        db.query("SELECT g.id,g.groupname,g.groupdesc,mine.grouprole,u.id,u.name,u.state,m.grouprole "
                 "FROM GroupUser mine JOIN AllGroup g ON g.id=mine.groupid "
                 "JOIN GroupUser m ON m.groupid=g.id JOIN User u ON u.id=m.userid "
                 "WHERE mine.userid=" + std::to_string(userId) + " ORDER BY g.id,u.id"), mysql_free_result);
    if (!result) { errorCode_ = db.errorCode(); return false; }
    while (MYSQL_ROW row = mysql_fetch_row(result.get())) {
        int id = std::atoi(row[0]);
        if (groups.empty() || groups.back().id != id) {
            Group g; g.id=id; g.name=row[1]; g.description=row[2]; g.role=row[3];
            groups.push_back(std::move(g));
        }
        groups.back().members.push_back(GroupMember{std::atoi(row[4]),row[5],row[6],row[7]});
    }
    return true;
}

bool GroupModel::recipients(int userId, int groupId, bool& isMember, std::vector<int>& users)
{
    isMember = false; users.clear(); errorCode_ = 0;
    MySQL db;
    if (!db.connect()) { errorCode_ = db.errorCode(); return false; }
    // 一次查询检查成员资格并取得该群成员快照；包含自己用于识别只有群主的群。
    std::unique_ptr<MYSQL_RES, decltype(&mysql_free_result)> result(
        db.query("SELECT m.userid FROM GroupUser me JOIN GroupUser m ON m.groupid=me.groupid "
            "WHERE me.userid=" + std::to_string(userId) + " AND me.groupid=" +
            std::to_string(groupId) + " ORDER BY m.userid"), mysql_free_result);
    if (!result) { errorCode_ = db.errorCode(); return false; }
    while (MYSQL_ROW row = mysql_fetch_row(result.get())) {
        isMember = true;
        int id = std::atoi(row[0]);
        if (id != userId) users.push_back(id);
    }
    return true;
}
