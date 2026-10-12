#include "chatserver.hpp"
#include "chatservice.hpp"
#include "json.hpp"
#include <functional>
#include <iostream>
#include <string>
#include <limits>
using namespace std;
using namespace placeholders;
using namespace muduo;
using namespace muduo::net;
using json = nlohmann::json;

ChatServer::ChatServer(
    EventLoop* loop,
    const InetAddress& listenAddr,
    const string& nameArg
)
    : _server(loop, listenAddr, nameArg)
    , _loop(loop)
{
    //注册链接回调
    _server.setConnectionCallback(
        bind(&ChatServer::onConnection, this, _1)
    );
    //注册消息回调
    _server.setMessageCallback(
        bind(&ChatServer::onMessage, this, _1, _2, _3)
    );
    //设置线程数量
    _server.setThreadNum(4);
}
void ChatServer::start()
{
    _server.start();
}
//上报链接相关信息的回调函数
void ChatServer::onConnection(const TcpConnectionPtr& conn)
{
    if(!conn->connected())
    {
        // TCP 已断开，交给业务层清理身份和在线状态。
        ChatService::instance()->clientCloseException(conn);
    }
}
//上报读写事件相关信息的回调函数
void ChatServer::onMessage(
    const TcpConnectionPtr& conn,
    Buffer* buf,
    Timestamp time
)
{
    constexpr size_t maxRequest = 64 * 1024;
    if (ChatService::instance()->isStopping()) { buf->retrieveAll(); return; }
    const char* end;
    while ((end = buf->findEOL()) != nullptr) {
        size_t length = static_cast<size_t>(end - buf->peek());
        if (length > maxRequest) {
            buf->retrieveAll(); conn->forceClose(); return;
        }
        std::string msg(buf->peek(), length);
        buf->retrieveUntil(end + 1);
        if (!msg.empty() && msg.back() == '\r') msg.pop_back();
        if (msg.empty()) continue;
        if (ChatService::instance()->isStopping()) { buf->retrieveAll(); return; }
        try {
            json js = json::parse(msg);
            if (!js.is_object() || !js.contains("msgid") || !js["msgid"].is_number_integer() ||
                js["msgid"] < 0 || js["msgid"] > std::numeric_limits<int>::max())
                throw std::invalid_argument("invalid msgid");
            auto handler = ChatService::instance()->getMsgHandler(js["msgid"].get<int>());
            handler(conn, js, time);
        } catch (const std::exception&) {
            conn->send(json({{"msgid",0},{"errno",1},{"errmsg","invalid JSON request or msgid"}}).dump() + "\n");
        }
    }
    // 无换行的半条请求保留在Muduo Buffer中；超过上限关闭连接。
    if (buf->readableBytes() > maxRequest) { buf->retrieveAll(); conn->forceClose(); }
}
