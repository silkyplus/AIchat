#pragma once
#include "json.hpp"
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

class ChatClient {
public:
    ChatClient() = default;
    ~ChatClient();
    ChatClient(const ChatClient&) = delete;
    ChatClient& operator=(const ChatClient&) = delete;
    bool connectTo(const std::string& ip, int port);
    bool request(const nlohmann::json& message, int expectedType, nlohmann::json& response);
    bool connected() const { return connected_.load(); }
    void stop();
    std::vector<nlohmann::json> takeNotifications();
private:
    void receiveLoop();
    bool sendAll(const std::string& data);
    int socket_ = -1;
    std::atomic<bool> connected_{false};
    std::thread receiver_;
    std::mutex mutex_;
    std::condition_variable condition_;
    int expectedType_ = -1;
    bool ready_ = false;
    nlohmann::json response_;
    std::vector<nlohmann::json> notifications_;
};
