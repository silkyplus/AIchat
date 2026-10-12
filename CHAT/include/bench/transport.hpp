#pragma once
#include "json.hpp"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <poll.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <thread>
#include <chrono>
#include <cmath>
#include <cerrno>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <vector>
#include <map>
#include <set>
#include <algorithm>
#include <stdexcept>
using nlohmann::json;
using Clock=std::chrono::steady_clock;
static volatile sig_atomic_t interrupted=0;
static void stop(int){interrupted=1;}
static double elapsed(Clock::time_point t){return std::chrono::duration<double>(Clock::now()-t).count();}
static void require(bool ok,const std::string& s){if(!ok)throw std::runtime_error(s);}
struct Conn {
    int fd=-1,id=0;std::string in,out;size_t offset=0;
    Conn()=default;Conn(const Conn&)=delete;Conn& operator=(const Conn&)=delete;
    Conn(Conn&& c):fd(c.fd),id(c.id),in(std::move(c.in)),out(std::move(c.out)),offset(c.offset){c.fd=-1;}
    ~Conn(){if(fd>=0)close(fd);}
    void queue(const json& j){require(out.empty(),"send queue already occupied");out=j.dump()+"\n";offset=0;}
    void flush(){while(offset<out.size()){
        ssize_t n=send(fd,out.data()+offset,out.size()-offset,MSG_NOSIGNAL);
        if(n>0)offset+=n;
        else if(n<0 && errno==EINTR)continue;
        else if(n<0 && (errno==EAGAIN||errno==EWOULDBLOCK))return;
        else throw std::runtime_error("socket send failed");
    }out.clear();offset=0;}
    void read(){char b[8192];for(;;){ssize_t n=recv(fd,b,sizeof(b),0);
        if(n>0){in.append(b,n);require(in.size()<=4*1024*1024,"receive buffer limit");}
        else if(n==0)throw std::runtime_error("peer disconnected");
        else if(errno==EINTR)continue;
        else if(errno==EAGAIN||errno==EWOULDBLOCK)return;
        else throw std::runtime_error("socket receive failed");
    }}
    bool line(json& j){auto p=in.find('\n');if(p==std::string::npos)return false;
        std::string s=in.substr(0,p);in.erase(0,p+1);require(!s.empty(),"empty JSON frame");j=json::parse(s);return true;}
};
static void waitfd(int fd,short events,Clock::time_point start,double timeout){
    while(!interrupted){double left=timeout-elapsed(start);require(left>0,"operation timeout");
        pollfd p{fd,events,0};int n=poll(&p,1,std::min(100,int(left*1000)+1));
        if(n<0 && errno==EINTR)continue;require(n>=0,"poll failed");
        if(n){require(!(p.revents&(POLLERR|POLLNVAL)),"socket error");return;}
    }throw std::runtime_error("interrupted");
}
static Conn connectTo(const std::string& host,int port,double timeout){
    Conn c;c.fd=socket(AF_INET,SOCK_STREAM,0);require(c.fd>=0,"socket failed");
    require(fcntl(c.fd,F_SETFL,O_NONBLOCK)==0,"nonblocking setup failed");
    sockaddr_in a{};a.sin_family=AF_INET;a.sin_port=htons(port);require(inet_pton(AF_INET,host.c_str(),&a.sin_addr)==1,"host must be IPv4");
    auto t=Clock::now();int r=connect(c.fd,(sockaddr*)&a,sizeof(a));
    if(r<0){require(errno==EINPROGRESS,"connect failed");waitfd(c.fd,POLLOUT,t,timeout);int error=0;socklen_t len=sizeof(error);require(getsockopt(c.fd,SOL_SOCKET,SO_ERROR,&error,&len)==0 && !error,"connect failed");}
    return c;
}
static json request(Conn& c,const json& j,double timeout){
    c.queue(j);auto start=Clock::now();
    while(!c.out.empty()){c.flush();if(!c.out.empty())waitfd(c.fd,POLLOUT,start,timeout);}
    json reply;while(!c.line(reply)){waitfd(c.fd,POLLIN,start,timeout);c.read();}return reply;
}
static json stats(std::vector<double> v){
    if(v.empty())return {{"samples",0}};std::sort(v.begin(),v.end());double sum=0;for(double x:v)sum+=x;
    auto q=[&](double p){return v[size_t(std::ceil(p*v.size()))-1];};
    return {{"samples",v.size()},{"mean_ms",sum/v.size()},{"p50_ms",q(.5)},{"p95_ms",q(.95)},{"p99_ms",q(.99)},{"max_ms",v.back()}};
}

// Optional phase-boundary handshake with the local Python resource monitor.
// CLOCK_MONOTONIC is explicit so Python time.monotonic uses the same VM clock.
#include <sys/un.h>
#include <time.h>
#include <cstdlib>
static std::string monitorError;
static long long monotonicNs(){timespec t{};require(clock_gettime(CLOCK_MONOTONIC,&t)==0,"monotonic clock failed");return t.tv_sec*1000000000LL+t.tv_nsec;}
static void phaseEvent(const std::string& phase,const std::string& edge){
    const char* path=std::getenv("CHAT_BENCH_MONITOR");if(!path || !*path)return;
    Conn c;c.fd=socket(AF_UNIX,SOCK_STREAM,0);require(c.fd>=0,"monitor socket failed");
    require(fcntl(c.fd,F_SETFL,O_NONBLOCK)==0,"monitor nonblocking failed");
    sockaddr_un addr{};addr.sun_family=AF_UNIX;require(std::string(path).size()<sizeof(addr.sun_path),"monitor path too long");std::copy(path,path+std::string(path).size(),addr.sun_path);
    auto start=Clock::now();int r=connect(c.fd,reinterpret_cast<sockaddr*>(&addr),sizeof(addr));
    if(r<0){require(errno==EINPROGRESS,"monitor connect failed");waitfd(c.fd,POLLOUT,start,3);int error=0;socklen_t size=sizeof(error);require(getsockopt(c.fd,SOL_SOCKET,SO_ERROR,&error,&size)==0 && error==0,"monitor connect failed");}
    c.queue({{"pid",getpid()},{"phase",phase},{"edge",edge},{"monotonic_ns",monotonicNs()}});
    while(!c.out.empty()){c.flush();if(!c.out.empty())waitfd(c.fd,POLLOUT,start,3);}
    // Finish reporting even after SIGTERM; the monitor never blocks an interrupted run indefinitely.
    pollfd p{c.fd,POLLIN,0};int n;do{n=poll(&p,1,1000);}while(n<0 && errno==EINTR);
    char ack=0;require(n>0 && recv(c.fd,&ack,1,0)==1 && ack=='1',"monitor acknowledgement failed");
}
struct PhaseScope {
    std::string name;bool active=true;
    explicit PhaseScope(const std::string& n):name(n){phaseEvent(name,"begin");}
    void finish(){if(active){active=false;phaseEvent(name,"end");}}
    ~PhaseScope(){if(active){try{finish();}catch(const std::exception& e){monitorError=e.what();}}}
};
