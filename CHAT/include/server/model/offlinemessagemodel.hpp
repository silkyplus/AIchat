#pragma once
#include <string>
#include <vector>
class OfflineMessageModel {
public:
    bool insert(int userId, const std::string& message);
    // 同一事务内读取并删除这一批记录；不是客户端确认投递。
    bool take(int userId, std::vector<std::string>& messages);
    const std::string& error() const { return error_; }
private:
    std::string error_;
};
