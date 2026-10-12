#ifndef CHATSERVICE_HPP
#define CHATSERVICE_HPP
#include<muduo/net/EventLoop.h>
#include<muduo/net/InetAddress.h>
#include<muduo/net/TcpConnection.h>
#include<map>
#include<string>
#include<unordered_map>
#include<functional>
#include<mutex>
#include<atomic>
#include"json.hpp"
#include "redis/redis.hpp"
using json = nlohmann::json;

//表示处理消息的回调函数类型
using MsgHandler = std::function<void(const muduo::net::TcpConnectionPtr& conn, const json& js, muduo::Timestamp time)>;
class ChatService
{
public:
    //获取单例对象的接口函数
    static ChatService* instance();
    //处理登录业务
    void login(const muduo::net::TcpConnectionPtr &conn, const json &js, muduo::Timestamp time);
    //处理注册业务
    void reg(const muduo::net::TcpConnectionPtr &conn, const json &js, muduo::Timestamp time);
    void oneChat(const muduo::net::TcpConnectionPtr& conn, const json& js, muduo::Timestamp time);
    void addFriend(const muduo::net::TcpConnectionPtr& conn, const json& js, muduo::Timestamp time);
    void createGroup(const muduo::net::TcpConnectionPtr& conn, const json& js, muduo::Timestamp time);
    void addGroup(const muduo::net::TcpConnectionPtr& conn, const json& js, muduo::Timestamp time);
    void groupChat(const muduo::net::TcpConnectionPtr& conn, const json& js, muduo::Timestamp time);
    void friendList(const muduo::net::TcpConnectionPtr& conn, const json& js, muduo::Timestamp time);
    void groupList(const muduo::net::TcpConnectionPtr& conn, const json& js, muduo::Timestamp time);
    //获取消息对应的处理器
    MsgHandler getMsgHandler(int msgid);
    // 返回 shared_ptr 副本，调用者不持锁使用连接；未找到时返回空指针。
    muduo::net::TcpConnectionPtr findConnection(int userId);
    int findUserId(const muduo::net::TcpConnectionPtr& conn);
    // 由 Muduo 的断开回调调用；未登录连接或已清理连接直接返回。
    void clientCloseException(const muduo::net::TcpConnectionPtr& conn);
    bool initRedis(muduo::net::EventLoop* loop);
    void stopRedis() { _redis.stop(); }
    void beginStop() { _stopping.store(true); }
    bool isStopping() const { return _stopping.load(); }
    // 只能在 TcpServer 析构、worker 全部结束后调用。
    void clearConnections();
private:
    std::atomic<bool> _stopping{false};
    bool bindConnection(int userId, const muduo::net::TcpConnectionPtr& conn);
    ChatService();
    enum class DeliveryResult { Local, Remote, Offline, Failed };
    DeliveryResult routeMessage(int userId, const json& message, std::string& error);
    void handleRedisMessage(const std::string& channel, const std::string& payload);
    std::string userChannel(int userId) const;
    Redis _redis;
    std::string _redisPrefix = "chat:user:";
    std::mutex _connMutex;
    std::unordered_map<int, muduo::net::TcpConnectionPtr> _userConnMap;
    //存储消息id和其对应的业务处理方法
    std::unordered_map<int, MsgHandler> _msgHandlerMap;
};

#endif
