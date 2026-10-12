#pragma once
#include "model/user.hpp"

class UserModel {
public:
    bool insert(User& user);
    // true 表示查询成功；不存在的用户 id 为 -1。false 表示数据库错误。
    bool query(int id, User& user);
    bool updateState(const User& user);
    // SQL 成功与是否取得登录资格分别返回，避免并发重复登录。
    bool trySetOnline(int id, bool& acquired);
    bool resetStateForServer();
    const std::string& error() const { return error_; }
    unsigned int errorCode() const { return errorCode_; }
private:
    unsigned int errorCode_ = 0;
    std::string error_;
};
