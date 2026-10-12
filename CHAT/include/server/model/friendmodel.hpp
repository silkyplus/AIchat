#pragma once
#include <string>
#include <vector>
struct FriendInfo {
    int id;
    std::string name;
    std::string state;
};
enum class AddFriendResult { Success, Duplicate, NotFound, Invalid, DatabaseError };
class FriendModel {
public:
    AddFriendResult add(int userId, int friendId);
    bool query(int userId, std::vector<FriendInfo>& friends);
    unsigned int errorCode() const { return errorCode_; }
private:
    unsigned int errorCode_ = 0;
};
