#include "db/db.hpp"
#include <cassert>
#include <thread>
#include <atomic>
#include <vector>
#include <memory>
#include <iostream>
#include <cstdlib>
#include <unistd.h>
long value(MySQL& db,const std::string& q) {
    MYSQL_RES* r=db.query(q); assert(r); MYSQL_ROW row=mysql_fetch_row(r); assert(row);
    long n=std::strtol(row[0],nullptr,10); mysql_free_result(r); return n;
}
int main() {
    const char* database=std::getenv("CHAT_DB_NAME");
    if(!database || std::string(database)!="chat_module_test") {
        std::cerr << "Set CHAT_DB_NAME=chat_module_test\n"; return 1;
    }
    setenv("CHAT_DB_POOL","1",1); setenv("CHAT_DB_POOL_SIZE","2",1);
    long id;
    { MySQL db; assert(db.connect()); id=value(db,"SELECT CONNECTION_ID()"); }
    { MySQL db; assert(db.connect()); assert(value(db,"SELECT CONNECTION_ID()")==id);
      assert(value(db,"SELECT LENGTH('你好')")==6); }
    std::string name="pool_rollback_"+std::to_string(getpid());
    { MySQL db; assert(db.connect()); assert(db.update("START TRANSACTION"));
      assert(db.update("INSERT INTO User(name,password,state) VALUES ('"+name+"','test','offline')")); }
    { MySQL db; assert(db.connect()); assert(value(db,"SELECT COUNT(*) FROM User WHERE name='"+name+"'")==0); }
    {
      MySQL first,second; assert(first.connect() && second.connect());
      assert(value(first,"SELECT CONNECTION_ID()")!=value(second,"SELECT CONNECTION_ID()"));
      MySQL third; assert(!third.connect()); assert(third.error().find("timed out")!=std::string::npos);
    }
    std::atomic<int> passed(0); std::vector<std::thread> threads;
    for(int t=0;t<8;++t) threads.emplace_back([&]{ for(int i=0;i<20;++i) {
        MySQL db; assert(db.connect()); assert(value(db,"SELECT 42")==42); ++passed;
    }});
    for(auto& t:threads)t.join(); assert(passed==160);
    { MySQL victim,killer; assert(victim.connect() && killer.connect());
      long dead=value(victim,"SELECT CONNECTION_ID()");
      assert(killer.update("KILL CONNECTION "+std::to_string(dead)));
      assert(!victim.query("SELECT 1")); }
    for(int i=0;i<3;++i) { MySQL db; assert(db.connect()); assert(value(db,"SELECT 1")==1); }
    std::cout << "PASS reuse, utf8mb4, transaction rollback, exclusive leases, bounded timeout, concurrency, broken connection disposal\n";
}
