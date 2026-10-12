#include "redis/redis.hpp"
#include <cstdlib>
#include <iostream>
#include <poll.h>
#include <unistd.h>
#include <cerrno>

std::string setting(const char* name, const char* fallback)
{
    const char* value = std::getenv(name); return value ? value : fallback;
}
int main(int argc, char* argv[])
{
    if (argc < 3 || (std::string(argv[1]) != "subscribe" && std::string(argv[1]) != "publish") ||
        (std::string(argv[1]) == "publish" && argc != 4) || (std::string(argv[1]) == "subscribe" && argc != 3)) {
        std::cerr << "用法: RedisDemo subscribe 频道 | RedisDemo publish 频道 '消息内容'\n"; return 1;
    }
    int port;
    try {
        std::string value = setting("CHAT_REDIS_PORT", "6379"); size_t used;
        port = std::stoi(value, &used);
        if (used != value.size() || port < 1 || port > 65535) throw std::invalid_argument("port");
    } catch (...) { std::cerr << "CHAT_REDIS_PORT必须为1-65535。\n"; return 1; }
    Redis redis;
    if (!redis.connect(setting("CHAT_REDIS_HOST", "127.0.0.1"), port,
        [](const std::string& channel, const std::string& message) {
            std::cout << "收到 [" << channel << "]: " << message << std::endl;
        }, setting("CHAT_REDIS_PASSWORD", ""))) {
        std::cerr << redis.error() << '\n'; return 1;
    }
    if (std::string(argv[1]) == "publish") {
        long long count = redis.publish(argv[2], argv[3]);
        if (count < 0) { std::cerr << "发布失败: " << redis.error() << '\n'; return 1; }
        std::cout << "发布成功，Redis订阅连接数: " << count << '\n'; return 0;
    }
    if (!redis.subscribe(argv[2])) { std::cerr << "订阅失败: " << redis.error() << '\n'; return 1; }
    std::cout << "订阅成功 [" << argv[2] << "]，按回车退出。" << std::endl;
    while (redis.connected()) {
        pollfd fd{STDIN_FILENO, POLLIN, 0};
        int n = poll(&fd, 1, 100);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) break;
        if (n > 0) {
            char ch;
            if (read(STDIN_FILENO, &ch, 1) <= 0 || ch == '\n') {
                bool ok = redis.unsubscribe(argv[2]); redis.stop();
                if (!ok) { std::cerr << "取消订阅失败: " << redis.error() << '\n'; return 1; }
                std::cout << "已取消订阅并退出。\n"; return 0;
            }
        }
    }
    std::cerr << "Redis连接已断开: " << redis.error() << '\n'; return 1;
}
