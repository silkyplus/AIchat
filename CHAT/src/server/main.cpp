#include "chatserver.hpp"
#include "chatservice.hpp"
#include "model/usermodel.hpp"
#include "node.hpp"
#include <muduo/net/Channel.h>
#include <sys/signalfd.h>
#include <sys/file.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <pthread.h>
#include <iostream>
#include <stdexcept>
#include <cctype>

namespace {
class FileDescriptor {
public:
    explicit FileDescriptor(int value) : value_(value) {}
    ~FileDescriptor() { if (value_ >= 0) close(value_); }
    int get() const { return value_; }
    FileDescriptor(const FileDescriptor&) = delete;
    FileDescriptor& operator=(const FileDescriptor&) = delete;
private:
    int value_;
};
}
int main(int argc, char* argv[])
{
    int port = 6000;
    if (argc > 2) { std::cerr << "Usage: ChatServer [port]\n"; return 1; }
    if (argc == 2) {
        try {
            std::string input(argv[1]); size_t end = 0;
            port = std::stoi(input, &end);
            if (end != input.size() || port < 1 || port > 65535) throw std::invalid_argument("port");
        } catch (const std::exception&) { std::cerr << "Invalid port\n"; return 1; }
    }
    if (!std::getenv("CHAT_SERVER_ID")) {
        const std::string defaultNode = "node-" + std::to_string(port);
        if (setenv("CHAT_SERVER_ID", defaultNode.c_str(), 0) != 0) return 1;
    }
    const std::string node = currentNodeId();
    if (node.empty() || node.size() > 64) { std::cerr << "Invalid CHAT_SERVER_ID\n"; return 1; }
    for (unsigned char c : node) if (!(std::isalnum(c) || c == '-' || c == '_')) {
        std::cerr << "CHAT_SERVER_ID accepts letters, digits, underscore and hyphen\n"; return 1;
    }
    // 文件锁在整个服务生命周期持有；不 unlink，避免锁住不同 inode。
    const std::string lockPath = "/tmp/chatserver-" + std::to_string(getuid()) + "-" + node + ".lock";
    FileDescriptor nodeLock(open(lockPath.c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600));
    if (nodeLock.get() < 0 || flock(nodeLock.get(), LOCK_EX | LOCK_NB) != 0) {
        std::cerr << "Node already running or lock unavailable: " << node << "\n"; return 1;
    }
    // 在创建任何 worker 之前屏蔽信号，worker 继承掩码；由 signalfd 接收。
    sigset_t mask; sigemptyset(&mask); sigaddset(&mask, SIGINT); sigaddset(&mask, SIGTERM);
    if (pthread_sigmask(SIG_BLOCK, &mask, nullptr) != 0) return 1;
    FileDescriptor signalFd(signalfd(-1, &mask, SFD_NONBLOCK | SFD_CLOEXEC));
    if (signalFd.get() < 0) { std::cerr << "signalfd failed\n"; return 1; }
    UserModel users;
    if (!users.resetStateForServer()) {
        std::cerr << "Startup recovery failed; database code=" << users.errorCode() << "\n"; return 1;
    }
    std::cout << "Startup recovery complete node=" << node << std::endl;
    muduo::net::EventLoop loop;
    if (!ChatService::instance()->initRedis(&loop)) {
        std::cerr << "Redis initialization failed; server not started\n"; return 1;
    }
    muduo::net::Channel signalChannel(&loop, signalFd.get());
    signalChannel.setReadCallback([&](muduo::Timestamp) {
        signalfd_siginfo info;
        while (read(signalFd.get(), &info, sizeof(info)) == sizeof(info)) {
            std::cout << "Shutdown requested signal=" << info.ssi_signo << " node=" << node << std::endl;
            ChatService::instance()->beginStop();
            loop.quit();
        }
    });
    signalChannel.enableReading();
    {
        muduo::net::InetAddress addr("127.0.0.1", static_cast<uint16_t>(port));
        ChatServer server(&loop, addr, "ChatServer");
        server.start(); loop.loop();
        // 主EventLoop还存在、worker尚未析构时停止Redis，防止回调访问已销毁的loop。
        ChatService::instance()->beginStop();
        ChatService::instance()->stopRedis();
    } // TcpServer 析构并结束 worker，之后不再有登录写入。
    signalChannel.disableAll(); signalChannel.remove();
    ChatService::instance()->clearConnections();
    if (!users.resetStateForServer()) {
        std::cerr << "Shutdown cleanup failed; database code=" << users.errorCode() << "\n"; return 1;
    }
    std::cout << "Shutdown cleanup complete node=" << node << std::endl;
    return 0;
}
