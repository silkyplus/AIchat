#include "redis/redis.hpp"
#include <sys/socket.h>
#include <poll.h>
#include <cerrno>
#include <chrono>
#include <memory>

namespace {
thread_local const Redis* receivingRedis = nullptr;
using Reply = std::unique_ptr<redisReply, decltype(&freeReplyObject)>;
redisContext* openConnection(const std::string& host, int port, const std::string& password)
{
    timeval timeout{1, 0};
    redisContext* c = redisConnectWithTimeout(host.c_str(), port, timeout);
    if (!c) return nullptr;
    if (c->err || redisSetTimeout(c, timeout) != REDIS_OK) { redisFree(c); return nullptr; }
    if (!password.empty()) {
        Reply auth(static_cast<redisReply*>(redisCommand(c, "AUTH %b", password.data(), password.size())), freeReplyObject);
        if (!auth || auth->type != REDIS_REPLY_STATUS || std::string(auth->str, auth->len) != "OK") {
            redisFree(c); return nullptr;
        }
    }
    Reply pong(static_cast<redisReply*>(redisCommand(c, "PING")), freeReplyObject);
    if (!pong || pong->type != REDIS_REPLY_STATUS || std::string(pong->str, pong->len) != "PONG") {
        redisFree(c); return nullptr;
    }
    return c;
}
std::string text(const redisReply* r)
{
    return r && r->type == REDIS_REPLY_STRING ? std::string(r->str, r->len) : std::string();
}
}
Redis::~Redis()
{
    stop();
    if (publisher_) redisFree(publisher_);
    if (subscriber_) redisFree(subscriber_);
}
bool Redis::connect(const std::string& host, int port, Handler handler, const std::string& password)
{
    if (publisher_ || subscriber_ || host.empty() || port < 1 || port > 65535 || !handler) return false;
    publisher_ = openConnection(host, port, password);
    subscriber_ = openConnection(host, port, password);
    if (!publisher_ || !subscriber_) { fail("Redis connection/authentication failed"); return false; }
    handler_ = std::move(handler);
    running_ = true;
    try { receiver_ = std::thread(&Redis::receiveLoop, this); }
    catch (...) { fail("Redis receiver thread failed"); return false; }
    return true;
}
std::string Redis::error() const
{
    std::lock_guard<std::mutex> lock(stateMutex_); return error_;
}
void Redis::fail(const std::string& reason)
{
    { std::lock_guard<std::mutex> lock(stateMutex_); error_ = reason; running_ = false; }
    // 出错后立即撤销服务端订阅，避免失效接收线程仍被PUBLISH计为订阅者。
    if (subscriber_) shutdown(subscriber_->fd, SHUT_RDWR);
    changed_.notify_all();
}
void Redis::stop()
{
    running_ = false;
    // context/FD 保留到接收线程退出之后；不在此处 redisFree。
    if (subscriber_) shutdown(subscriber_->fd, SHUT_RDWR);
    changed_.notify_all();
    if (receiver_.joinable()) receiver_.join();
}
bool Redis::subscribe(const std::string& channel) { return subscription("subscribe", channel); }
bool Redis::unsubscribe(const std::string& channel) { return subscription("unsubscribe", channel); }
bool Redis::subscription(const std::string& action, const std::string& channel)
{
    if (channel.empty() || channel.size() > 256 || receivingRedis == this) return false;
    std::lock_guard<std::mutex> command(commandMutex_); // 每次只有一条订阅操作等待 ACK。
    std::unique_lock<std::mutex> lock(stateMutex_);
    if (!running_) return false;
    action_ = action; channel_ = channel; acknowledged_ = false; queued_ = true;
    bool ready = changed_.wait_for(lock, std::chrono::seconds(3), [&] { return acknowledged_ || !running_; });
    if (!ready) {
        error_ = "Redis subscription acknowledgement timed out"; running_ = false;
        // 超时后放弃此连接，避免迟到 ACK 被下一次订阅误认。
        shutdown(subscriber_->fd, SHUT_RDWR);
    }
    return ready && acknowledged_ && running_;
}
long long Redis::publish(const std::string& channel, const std::string& message)
{
    if (channel.empty() || channel.size() > 256 || message.size() > 64 * 1024) return -1;
    std::lock_guard<std::mutex> lock(publishMutex_);
    if (!running_) return -1;
    Reply reply(static_cast<redisReply*>(redisCommand(publisher_, "PUBLISH %b %b", channel.data(), channel.size(), message.data(), message.size())), freeReplyObject);
    if (!reply || reply->type != REDIS_REPLY_INTEGER) { fail("Redis publish failed"); return -1; }
    return reply->integer;
}
void Redis::receiveLoop()
{
    receivingRedis = this;
    try {
        while (running_) {
            std::string action, channel;
            {
                std::lock_guard<std::mutex> lock(stateMutex_);
                if (queued_) { action = action_; channel = channel_; queued_ = false; }
            }
            if (!action.empty()) {
                const char* command = action == "subscribe" ? "SUBSCRIBE %b" : "UNSUBSCRIBE %b";
                if (redisAppendCommand(subscriber_, command, channel.data(), channel.size()) != REDIS_OK) {
                    fail("Redis subscription command failed"); break;
                }
                int done = 0;
                while (!done && running_) {
                    if (redisBufferWrite(subscriber_, &done) != REDIS_OK) { fail("Redis subscription write failed"); break; }
                }
            }
            if (!running_) break;
            pollfd fd{subscriber_->fd, POLLIN, 0};
            int n = poll(&fd, 1, 100); // 同时检查动态订阅队列与退出状态。
            if (n < 0 && errno == EINTR) continue;
            if (n < 0) { fail("Redis subscription poll failed"); break; }
            if (!n) continue;
            if (redisBufferRead(subscriber_) != REDIS_OK) { if (running_) fail("Redis subscription disconnected"); break; }
            while (running_) {
                void* raw = nullptr;
                if (redisGetReplyFromReader(subscriber_, &raw) != REDIS_OK) { fail("Redis subscription parse failed"); break; }
                if (!raw) break;
                Reply reply(static_cast<redisReply*>(raw), freeReplyObject);
                if (reply->type != REDIS_REPLY_ARRAY || reply->elements != 3) { fail("Redis unexpected subscription reply"); break; }
                std::string kind = text(reply->element[0]);
                std::string receivedChannel = text(reply->element[1]);
                if (kind == "message" && reply->element[2]->type == REDIS_REPLY_STRING) {
                    try { handler_(receivedChannel, text(reply->element[2])); }
                    catch (...) { /* 回调异常不能终止接收线程；业务层自行记录处理失败。 */ }
                } else if (kind == "subscribe" || kind == "unsubscribe") {
                    std::lock_guard<std::mutex> lock(stateMutex_);
                    if (kind != action_ || receivedChannel != channel_) { running_ = false; error_ = "Redis unexpected subscription acknowledgement"; }
                    else acknowledged_ = true;
                    changed_.notify_all();
                } else { fail("Redis invalid subscription reply"); break; }
            }
        }
    } catch (...) { fail("Redis receiver failed"); }
    running_ = false; changed_.notify_all();
    receivingRedis = nullptr;
}
