#include "redis/redis.hpp"
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>
#include <unistd.h>

void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
int main(int argc, char* argv[])
{
    if (argc != 3) { std::cerr << "用法: RedisPubSubTest host port\n"; return 1; }
    try {
        std::string host = argv[1]; int port = std::stoi(argv[2]);
        const char* secret = std::getenv("CHAT_REDIS_PASSWORD"); std::string password = secret ? secret : "";
        std::mutex mutex; std::condition_variable changed;
        std::vector<std::pair<std::string,std::string>> messages;
        auto handler = [&](const std::string& ch, const std::string& msg) {
            if (msg == "throw-callback") throw std::runtime_error("test callback");
            std::lock_guard<std::mutex> lock(mutex); messages.emplace_back(ch,msg); changed.notify_all();
        };
        auto waitCount = [&](size_t count) {
            std::unique_lock<std::mutex> lock(mutex);
            require(changed.wait_for(lock,std::chrono::seconds(3),[&]{return messages.size()>=count;}), "message wait timeout");
        };
        Redis a,b,p;
        require(a.connect(host,port,handler,password),"subscriber A connect");
        require(b.connect(host,port,handler,password),"subscriber B connect");
        require(p.connect(host,port,[](const std::string&,const std::string&){},password),"publisher connect");
        std::string channel="test:module131:"+std::to_string(getpid()), other=channel+":other";
        require(p.publish(channel,"no subscriber")==0,"zero subscribers");
        require(a.subscribe(channel),"subscribe A ack");
        require(a.subscribe(channel),"repeated subscribe ack");
        require(b.subscribe(channel),"subscribe B ack");
        std::string payload="你好，Redis 空格 100%";payload.push_back('\0');payload+="tail\nline";
        require(p.publish(channel,payload)==2,"two subscribers");waitCount(2);
        { std::lock_guard<std::mutex> lock(mutex);require(messages[0].first==channel && messages[0].second==payload && messages[1].second==payload,"binary safe payload"); }
        require(a.unsubscribe(channel),"unsubscribe A ack");
        require(p.publish(channel,"only B")==1,"one subscriber");waitCount(3);
        require(a.subscribe(other),"dynamic second channel");
        require(p.publish(other,"other channel")==1,"channel isolation");waitCount(4);
        require(p.publish(other,"throw-callback")==1,"callback throw publish");
        require(p.publish(other,"after callback exception")==1,"callback continues");waitCount(5);
        std::vector<std::thread> publishers;std::atomic<bool> ok{true};
        for(int i=0;i<4;++i) publishers.emplace_back([&]{for(int j=0;j<20;++j)if(p.publish(channel,"concurrent")!=1)ok=false;});
        for(auto& t:publishers)t.join();require(ok,"concurrent publisher serialization");waitCount(85);
        require(a.unsubscribe(other),"unsubscribe other");require(b.unsubscribe(channel),"unsubscribe B");
        require(b.unsubscribe(channel),"unsubscribe absent channel ack");
        require(p.publish(channel,"after unsubscribe")==0,"no delivery after unsubscribe");
        require(a.subscribe(channel),"subscribe after all channels removed");require(p.publish(channel,"resubscribe")==1,"resubscribe count");waitCount(86);
        require(a.unsubscribe(channel),"final unsubscribe");
        auto before=std::chrono::steady_clock::now();a.stop();a.stop();b.stop();p.stop();
        require(std::chrono::steady_clock::now()-before<std::chrono::seconds(2),"idle stop joins promptly");
        require(!a.subscribe(channel) && p.publish(channel,"stopped")==-1,"operation after stop fails");
        {std::lock_guard<std::mutex> lock(mutex);require(messages.size()==86,"unexpected duplicate callback");}
        Redis invalid;require(!invalid.connect(host,0,handler),"invalid port rejected");
        std::cout << "PASS subscribe ACK, dynamic channels, binary/UTF-8 payload, fanout, unsubscribe/resubscribe, callback exception, concurrent publish, clean stop\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}
}
