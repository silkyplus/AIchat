#include "db/db.hpp"
#include <cstdlib>
#include <stdexcept>
#include <chrono>
#include <mutex>
#include <condition_variable>
#include <vector>
#include <fstream>
#include <unistd.h>

namespace {
using Clock = std::chrono::steady_clock;
const char* env(const char* key, const char* fallback) {
    const char* value = std::getenv(key); return value ? value : fallback;
}
struct Metrics {
    std::mutex mutex;
    struct Counter { unsigned long long calls=0; double ms=0; } connect, acquire, sql;
    void add(Counter& c, Clock::time_point begin) {
        std::lock_guard<std::mutex> lock(mutex);
        ++c.calls; c.ms += std::chrono::duration<double,std::milli>(Clock::now()-begin).count();
    }
    ~Metrics() {
        std::string path=env("CHAT_DB_PROFILE", ""); if(path.empty()) return;
        auto pos=path.find("%p"); if(pos!=std::string::npos) path.replace(pos,2,std::to_string(getpid()));
        std::ofstream f(path);
        f << "{\"scope\":\"whole process including preparation\",\"pool\":"
          << (std::string(env("CHAT_DB_POOL","1"))=="0"?"false":"true");
        const char* names[]={"connect","acquire","sql"}; Counter* values[]={&connect,&acquire,&sql};
        for(int i=0;i<3;++i) f << ",\"" << names[i] << "\":{\"calls\":" << values[i]->calls << ",\"total_ms\":" << values[i]->ms << "}";
        f << "}\n";
    }
};
Metrics& metrics() { static Metrics m; return m; }
struct Timer {
    Metrics::Counter* counter;
    Clock::time_point begin=Clock::now();
    explicit Timer(Metrics::Counter& c):counter(std::getenv("CHAT_DB_PROFILE")?&c:nullptr) {}
    ~Timer() { if(counter) metrics().add(*counter,begin); }
};
struct Pool {
    std::mutex mutex; std::condition_variable changed;
    std::vector<MYSQL*> idle; size_t total=0, limit=8;
    Pool() {
        const char* value=env("CHAT_DB_POOL_SIZE","8"); char* end=nullptr;
        long n=std::strtol(value,&end,10);
        if(!end || *end || n<1 || n>64) throw std::invalid_argument("CHAT_DB_POOL_SIZE must be 1..64");
        limit=static_cast<size_t>(n);
        idle.reserve(limit); // 归还连接时无需分配内存，析构路径不因扩容抛异常。
    }
    ~Pool() { for(auto c:idle) mysql_close(c); }
    MYSQL* take(bool& fresh) {
        std::unique_lock<std::mutex> lock(mutex);
        if(!changed.wait_for(lock,std::chrono::seconds(3),[this]{return !idle.empty() || total<limit;})) { fresh=false; return nullptr; }
        if(!idle.empty()) { auto c=idle.back(); idle.pop_back(); fresh=false; return c; }
        ++total; fresh=true; return nullptr;
    }
    void give(MYSQL* c, bool healthy) {
        if(c && !healthy) mysql_close(c);
        { std::lock_guard<std::mutex> lock(mutex);
          if(healthy) idle.push_back(c); else --total; }
        changed.notify_one();
    }
};
Pool& pool() { static Pool p; return p; }
}
MySQL::MySQL() : connection_(nullptr) {}
MySQL::~MySQL() {
    if(!connection_) return;
    // 当前模型只通过 START TRANSACTION 开启事务；未完成事务绝不能传给下一租户。
    if(transaction_ && mysql_rollback(connection_)!=0) broken_=true;
    if(pooled_) pool().give(connection_,!broken_); else mysql_close(connection_);
}
bool MySQL::connect() {
    if(connection_) return true;
    connectErrorCode_=0;
    Timer lease(metrics().acquire);
    pooled_=std::string(env("CHAT_DB_POOL","1"))!="0";
    bool fresh=true;
    if(pooled_) {
        try { connection_=pool().take(fresh); }
        catch(const std::exception& e) { error_=e.what(); return false; }
        if(!fresh) {
            if(!connection_) { error_="database connection pool wait timed out"; return false; }
            error_.clear(); return true;
        }
    }
    Timer opening(metrics().connect);
    connection_=mysql_init(nullptr);
    if(!connection_) { if(pooled_) pool().give(nullptr,false); error_="mysql_init failed"; return false; }
    unsigned int timeout=3;
    mysql_options(connection_,MYSQL_OPT_CONNECT_TIMEOUT,&timeout);
    mysql_options(connection_,MYSQL_OPT_READ_TIMEOUT,&timeout);
    mysql_options(connection_,MYSQL_OPT_WRITE_TIMEOUT,&timeout);
    mysql_options(connection_,MYSQL_SET_CHARSET_NAME,"utf8mb4");
    if(!mysql_real_connect(connection_,env("CHAT_DB_HOST","127.0.0.1"),env("CHAT_DB_USER","chat_module_test"),
        env("CHAT_DB_PASSWORD",""),env("CHAT_DB_NAME","chat_module_test"),3306,nullptr,0)) {
        error_=mysql_error(connection_); connectErrorCode_=mysql_errno(connection_);
        if(pooled_) pool().give(connection_,false); else mysql_close(connection_);
        connection_=nullptr; return false;
    }
    error_.clear(); return true;
}
bool MySQL::update(const std::string& sql) {
    if(!connection_) { error_="connection unavailable"; return false; }
    Timer timing(metrics().sql);
    if(mysql_query(connection_,sql.c_str())) {
        error_=mysql_error(connection_);
        unsigned int code=mysql_errno(connection_);
        if(code>=2000) broken_=true;
        return false;
    }
    if(sql=="START TRANSACTION" || sql=="BEGIN") transaction_=true;
    if(sql=="COMMIT" || sql=="ROLLBACK") transaction_=false;
    error_.clear(); return true;
}
MYSQL_RES* MySQL::query(const std::string& sql) {
    if(!update(sql)) return nullptr;
    MYSQL_RES* result=mysql_store_result(connection_);
    if(!result) { error_=mysql_error(connection_); broken_=true; }
    return result;
}
std::string MySQL::escape(const std::string& value) {
    std::string escaped(value.size()*2+1,'\0');
    unsigned long count=mysql_real_escape_string(connection_,&escaped[0],value.data(),value.size());
    if(count==static_cast<unsigned long>(-1)) throw std::runtime_error(mysql_error(connection_));
    escaped.resize(count); return escaped;
}
unsigned long long MySQL::insertId() const { return mysql_insert_id(connection_); }
