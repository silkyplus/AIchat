#pragma once
#include "model/group.hpp"
enum class JoinGroupResult { Success, Duplicate, NotFound, Invalid, DatabaseError };
class GroupModel {
public:
    // 成功后写回群ID；创建群和登记群主使用同一事务。
    bool create(int creatorId, Group& group);
    JoinGroupResult join(int userId, int groupId);
    bool query(int userId, std::vector<Group>& groups);
    bool recipients(int userId, int groupId, bool& isMember, std::vector<int>& users);
    unsigned int errorCode() const { return errorCode_; }
private:
    unsigned int errorCode_ = 0;
};
