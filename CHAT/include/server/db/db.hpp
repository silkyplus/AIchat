#pragma once
#include <mysql/mysql.h>
#include <string>

// 每个实例独占一个连接租约；不要跨线程共享同一个实例。
class MySQL {
public:
    MySQL();
    ~MySQL();
    MySQL(const MySQL&) = delete;
    MySQL& operator=(const MySQL&) = delete;
    bool connect();
    bool update(const std::string& sql);
    // 返回结果由调用者 mysql_free_result 释放。
    MYSQL_RES* query(const std::string& sql);
    std::string escape(const std::string& value);
    unsigned long long insertId() const;
    unsigned long long affectedRows() const { return mysql_affected_rows(connection_); }
    unsigned int errorCode() const { return connection_ ? mysql_errno(connection_) : connectErrorCode_; }
    const std::string& error() const { return error_; }
private:
    MYSQL* connection_;
    std::string error_;
    bool pooled_ = false;
    bool broken_ = false;
    bool transaction_ = false;
    unsigned int connectErrorCode_ = 0;
};
