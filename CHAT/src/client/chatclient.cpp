#include "client/chatclient.hpp"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <fcntl.h>
#include <poll.h>
#include <cerrno>
#include <chrono>
#include <iostream>
#include <limits>

using json = nlohmann::json;
ChatClient::~ChatClient()
{
    stop();
    if (receiver_.joinable()) receiver_.join();
    if (socket_ >= 0) close(socket_);
}
bool ChatClient::connectTo(const std::string& ip, int port)
{
    if (socket_ >= 0) return false;
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(static_cast<uint16_t>(port));
    if (inet_pton(AF_INET, ip.c_str(), &address.sin_addr) != 1) return false;
    socket_ = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (socket_ < 0) return false;
    int flags = fcntl(socket_, F_GETFL, 0);
    if (flags < 0 || fcntl(socket_, F_SETFL, flags | O_NONBLOCK) < 0) return false;
    if (connect(socket_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        if (errno != EINPROGRESS) return false;
        pollfd descriptor{socket_, POLLOUT, 0};
        int status;
        do { status = poll(&descriptor, 1, 3000); } while (status < 0 && errno == EINTR);
        int error = 0; socklen_t length = sizeof(error);
        if (status <= 0 || getsockopt(socket_, SOL_SOCKET, SO_ERROR, &error, &length) < 0 || error) return false;
    }
    if (fcntl(socket_, F_SETFL, flags) < 0) return false;
    timeval timeout{5, 0};
    if (setsockopt(socket_, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) < 0) return false;
    connected_ = true;
    receiver_ = std::thread(&ChatClient::receiveLoop, this);
    return true;
}
std::vector<json> ChatClient::takeNotifications()
{
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<json> result; result.swap(notifications_); return result;
}
void ChatClient::stop()
{
    connected_ = false;
    if (socket_ >= 0) shutdown(socket_, SHUT_RDWR); // 唤醒阻塞 recv；join 后才 close，避免FD复用竞态。
    condition_.notify_all();
}
bool ChatClient::sendAll(const std::string& data)
{
    size_t offset = 0;
    while (offset < data.size()) {
        ssize_t n = send(socket_, data.data() + offset, data.size() - offset, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        offset += static_cast<size_t>(n);
    }
    return true;
}
bool ChatClient::request(const json& message, int expectedType, json& response)
{
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!connected_ || expectedType_ != -1) return false;
        expectedType_ = expectedType; ready_ = false;
    }
    if (!sendAll(message.dump() + "\n")) { stop(); return false; }
    std::unique_lock<std::mutex> lock(mutex_);
    bool notified = condition_.wait_for(lock, std::chrono::seconds(10), [&] { return ready_ || !connected_; });
    if (!notified || !ready_) {
        expectedType_ = -1;
        lock.unlock(); stop(); // 超时后关闭，避免迟到响应被下一请求误认。
        return false;
    }
    response = response_; expectedType_ = -1; ready_ = false;
    return true;
}
void ChatClient::receiveLoop()
{
    std::string pending;
    char bytes[8192];
    constexpr size_t maxResponse = 4 * 1024 * 1024;
    try {
        while (connected_) {
            ssize_t n = recv(socket_, bytes, sizeof(bytes), 0);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) break;
            pending.append(bytes, static_cast<size_t>(n));
            size_t end;
            while ((end = pending.find('\n')) != std::string::npos) {
                if (end > maxResponse) throw std::runtime_error("response too large");
                std::string line = pending.substr(0, end);
                pending.erase(0, end + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                if (line.empty()) continue;
                json message = json::parse(line);
                if (!message.is_object() || !message.contains("msgid") ||
                    !message["msgid"].is_number_integer() || message["msgid"] < 0 ||
                    message["msgid"] > std::numeric_limits<int>::max())
                    throw std::runtime_error("invalid response");
                std::lock_guard<std::mutex> lock(mutex_);
                if (message["msgid"].get<int>() == expectedType_ && !ready_) {
                    response_ = std::move(message); ready_ = true; condition_.notify_all();
                }
                else if (message["msgid"] == 5 || message["msgid"] == 13) {
                    notifications_.push_back(std::move(message));
                }
            }
            if (pending.size() > maxResponse) throw std::runtime_error("response too large");
        }
    } catch (const std::exception&) {
        // 连接关闭交由主线程显示，不在后台线程输出交错的菜单。
    }
    stop();
}
