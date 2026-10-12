#pragma once
#include <hiredis/hiredis.h>
#include <atomic>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

// 发布连接由互斥锁串行使用；订阅连接只由接收线程操作 hiredis。
// connect/stop/析构由拥有者线程调用；回调不得同步调用 subscribe/unsubscribe/stop。
class Redis {
public:
    using Handler = std::function<void(const std::string&, const std::string&)>;
    Redis() = default;
    ~Redis();
    Redis(const Redis&) = delete;
    Redis& operator=(const Redis&) = delete;
    bool connect(const std::string& host, int port, Handler handler, const std::string& password = "");
    bool subscribe(const std::string& channel);
    bool unsubscribe(const std::string& channel);
    // >=0 为 Redis 返回的订阅连接数，不是客户端收件确认；-1 表示失败。
    long long publish(const std::string& channel, const std::string& message);
    bool connected() const { return running_.load(); }
    std::string error() const;
    void stop();
private:
    bool subscription(const std::string& action, const std::string& channel);
    void receiveLoop();
    void fail(const std::string& reason);
    redisContext* publisher_ = nullptr;
    redisContext* subscriber_ = nullptr;
    Handler handler_;
    std::atomic<bool> running_{false};
    std::thread receiver_;
    std::mutex publishMutex_, commandMutex_;
    mutable std::mutex stateMutex_;
    std::condition_variable changed_;
    std::string action_, channel_, error_;
    bool queued_ = false, acknowledged_ = false;
};
