#pragma once
#include <string>
#include <utility>

class User {
public:
    int getId() const { return id_; }
    const std::string& getName() const { return name_; }
    const std::string& getPassword() const { return password_; }
    const std::string& getState() const { return state_; }
    void setId(int id) { id_ = id; }
    void setName(std::string name) { name_ = std::move(name); }
    // 存储密码哈希；哈希生成和校验由后续业务模块负责。
    void setPassword(std::string hash) { password_ = std::move(hash); }
    void setState(std::string state) { state_ = std::move(state); }
private:
    int id_ = -1;
    std::string name_;
    std::string password_;
    std::string state_ = "offline";
};
