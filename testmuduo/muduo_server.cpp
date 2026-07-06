/*
g++ -o server muduo_server.cpp -lmuduo_net -lmuduo_base -lpthread
muduo网络库给用户提供了两个主要的类
TcpServer :用于编写服务器
TcpClient :用于编写客户端

epoll+线程池
好处：把网络I/O的代码和业务代码区分开
业务代码暴露：用户的连接和断开，用户的可读写事件
*/
#include"../json.hpp"
using namespace nlohmann;
#include<muduo/net/TcpServer.h>
#include<muduo/net/EventLoop.h>
#include<iostream>
#include<functional>
#include<string>
#include<thread>
using namespace std;
using namespace muduo;
using namespace muduo::net;
using namespace placeholders;

/*
基于muduo网络库开发服务器程序
1.组合TCPServer对象
2.创建Eventloop事件循环对象的指针
3.明确TcpServer构造函数需要什么参数，输出ChatServer的构造函数
4.在当前服务器类的构造函数当中，注册处理连接的回调函数和处理读写事件的回调函数
5.设置服务器端的线程数量，muduo会自己分配I/O线程和业务线程
*/
class ChatServer
{
public:
    ChatServer(EventLoop *loop,//事件循环
                const InetAddress &listenAddr,//IP+Port
                const string &nameArg)//服务器名字
                :_server(loop,listenAddr,nameArg),
                _loop(loop)
    {
        //给服务器注册用户连接的创建和断开回调
        _server.setConnectionCallback(bind(&ChatServer::onConnection,this,_1));
        //给服务器注册用户读写事件的回调
        _server.setMessageCallback(bind(&ChatServer::onMessage,this,_1,_2,_3));
        //设置服务器端的线程数量
        _server.setThreadNum(4);//1个I/O线程，3个业务线程
    }
    //开启事件循环
    void start()//启动服务器
    {
        //启动服务器
        _server.start();
    }
private:
    //专门处理用户连接和断开的回调函数
    void onConnection(const TcpConnectionPtr &conn)
    {
        //peerAddress :客户端的IP+Port
        //localAddress :服务器端的IP+Port
        
        if(conn->connected())
        {
            //用户连接上
            cout<<conn->peerAddress().toIpPort()
            <<" -> "<<
            conn->localAddress().toIpPort()
            <<"state:online"
            <<endl;
        }
        else
        {
            //用户断开连接
            cout<<conn->peerAddress().toIpPort()
            <<" -> "<<
            conn->localAddress().toIpPort()
            <<"state:offline"
            <<endl;

            conn->shutdown();
            // _loop->quit();
        }

    }
    //专门处理用户读写事件的回调函数
    void onMessage(const TcpConnectionPtr &conn,//连接
                  Buffer *buffer,//读取数据的缓冲区
                  Timestamp time)//时间戳
    {
        //从缓冲区中读取数据
        string msg = buffer->retrieveAllAsString();
        //过滤空消息
        if (msg.empty()) {
            return;
        }
        //打印用户发送的数据
        cout<<"[ChatServer] onMessage :"<<msg
        <<"[Time] now Time\t"<<time.toFormattedString()<<endl;
        //给客户端发送数据
        try {
        json js = json::parse(msg);
        
        if (js.contains("msg_type")) {        // 先检查字段存在
            string type = js["msg_type"];
            
            if (type == "chat") {
                cout << "chat message received" << endl;
                conn->send("chat received");
                cout<<endl;
            }
            else if (type == "login") {
                cout << "login success!" << endl;
                conn->send("login success");
                cout<<endl;
            }
            else {
                conn->send("unknown msg_type");
                cout<<endl;
            }
        } else {
            conn->send("msg_type required");
            cout<<"111"<<endl;
        }
    } catch (const json::parse_error& e) {
        // 非法 JSON，不崩
        cout << "[ERROR] invalid json received!" << endl;
        conn->send("invalid json");
    }
    }
    TcpServer _server;//#1第一步
    EventLoop *_loop;//#2第二步
};
int main()
{
    EventLoop loop;
    InetAddress addr("127.0.0.1",6000);
    ChatServer server(&loop,addr,"ChatServer");
    server.start();
    loop.loop();//开启事件循环,调用epoll_wait,以阻塞方式等待新用户的连接，已连接的用户的读写事件等
}