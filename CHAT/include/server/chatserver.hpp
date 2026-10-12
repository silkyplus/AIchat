#ifndef CHATSERVER_HPP
#define CHATSERVER_HPP

#include<muduo/net/TcpServer.h>
#include<muduo/net/EventLoop.h>
#include<string>

//聊天服务器的主类
class ChatServer
{
public:
    ChatServer(
        muduo::net::EventLoop* loop,
        const muduo::net::InetAddress& listenAddr,
        const std::string& nameArg
    );
    //启动服务
    void start();
private:
    //上报链接信息的回调函数
    void onConnection(const muduo::net::TcpConnectionPtr& conn);
    //上报读写事件的回调函数
    void onMessage(const muduo::net::TcpConnectionPtr& conn, muduo::net::Buffer* buf, muduo::Timestamp time);
    muduo::net::TcpServer _server;//组合muduo库，实现服务器功能的类对象
    muduo::net::EventLoop* _loop;//指向事件循环的指针
};
#endif