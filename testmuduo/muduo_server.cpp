/*
muduo网络库给用户提供了两个主要的类
TcpServer :用于编写服务器
TcpClient :用于编写客户端

epoll+线程池
好处：把网络I/O的代码和业务代码区分开
业务代码暴露：用户的连接和断开，用户的可读写事件
*/

#include<muduo/nety/TcpServer.h>
#include<muduo/net/EventLoop.h>
#include<iostream>
using namespace std;
using namespace muduo;
using namespace muduo::net;
/*
基于muduo网络库开发服务器程序
1.组合TCPServer对象
2.创建Eventloop事件循环对象的指针
3.明确TcpServer构造函数需要什么参数，输出ChatServer的构造函数
*/
class ChatServer
{
public:
    ChatServer(Evenloop *loop,
                const InetAddress &listenAddr,
                const string &nameArg)
                :_server(loop,listenAddr,nameArg),
                _loop(loop)
    {
        //给服务器注册用户连接的创建和断开回调

        //给服务器注册用户读写事件的回调WD
    }
private:
    TcpServer _server;//#1第一步
    EventLoop *_loop;//#2第二步 
}
