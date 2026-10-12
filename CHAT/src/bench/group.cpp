#include "bench/transport.hpp"

static std::string bodyFor(const std::string& run,const std::string& phase,int seq,int bytes){
    std::string key=run+":"+phase+":"+std::to_string(seq)+"|";
    require(key.size()<=size_t(bytes),"message prefix too long");return key+std::string(bytes-key.size(),'x');
}
static int sequenceFor(const std::string& body,const std::string& run,const std::string& phase,int limit,int bytes){
    std::string prefix=run+":"+phase+":";if(body.compare(0,prefix.size(),prefix)!=0)return -1;
    auto end=body.find('|',prefix.size());if(end==std::string::npos)return -1;
    try{std::string number=body.substr(prefix.size(),end-prefix.size());size_t used=0;int seq=std::stoi(number,&used);
        if(used!=number.size() || seq<0 || seq>=limit || body!=bodyFor(run,phase,seq,bytes))return -1;return seq;
    }catch(...){return -1;}
}
static Conn loginAccount(const json& a,double timeout,bool retryOnline=false){
    auto start=Clock::now();
    do {
        require(!interrupted,"interrupted");
        Conn c=connectTo(a.value("host",std::string("127.0.0.1")),a.at("port"),timeout);c.id=a.at("id");
        json r=request(c,{{"msgid",1},{"id",c.id},{"password",a.at("password")}},timeout);
        if(r.value("msgid",0)==4 && r.value("errno",-1)==0){
            require(!r.contains("offline_error"),"offline retrieval unavailable");
            // Fetch mode consumes login payload separately; ordinary load accounts must be clean.
            require(!r.contains("offlinemsg") || r["offlinemsg"].empty(),"unexpected offline messages during preparation");return c;
        }
        require(retryOnline && r.value("errno",-1)==2,"login rejected");
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }while(elapsed(start)<timeout);
    throw std::runtime_error("login cleanup timeout");
}
static json barrier(const json& cfg,const std::vector<Conn>& cs,double timeout){
    json ids=json::array();for(auto& c:cs)ids.push_back(c.id);
    std::string ready=cfg.at("ready_file"),go=cfg.at("start_file"),tmp=ready+".tmp";
    std::ofstream f(tmp);require(bool(f),"ready file unavailable");f<<json({{"ids",ids}}).dump();f.close();require(bool(f)&&std::rename(tmp.c_str(),ready.c_str())==0,"ready file write failed");
    auto start=Clock::now();std::ifstream g;
    while(!interrupted && elapsed(start)<timeout){g.open(go);if(g)break;g.clear();std::this_thread::sleep_for(std::chrono::milliseconds(10));}
    require(!interrupted && bool(g),"ownership barrier timeout");json result;g>>result;return result;
}
int main(int argc,char** argv){
    signal(SIGINT,stop);signal(SIGTERM,stop);json report={{"schema",1},{"ok",false}};std::string output;int code=1;
    try{
        require(argc==2,"Usage: ChatGroupBench config.json");std::ifstream f(argv[1]);require(bool(f),"config unavailable");json cfg;f>>cfg;output=cfg.at("report");
        std::string mode=cfg.at("mode"),run=cfg.at("run_id");int count=cfg.value("messages",20),bytes=cfg.value("message_bytes",128),warmup=cfg.value("warmup",2),gid=cfg.value("group_id",0);
        double timeout=cfg.value("timeout_seconds",10.0),maxSeconds=cfg.value("max_seconds",60.0);auto accounts=cfg.at("accounts");
        require(mode=="group_online" || mode=="offline_group_store" || mode=="offline_single_store" || mode=="offline_fetch","invalid mode");
        require(accounts.is_array() && accounts.size()>=2 && accounts.size()<=51,"expected sender and 1..50 recipients");
        size_t recipients=accounts.size()-1;
        require(count>0 && count<=10000 && recipients*size_t(count)<=100000 && bytes>=64 && bytes<=4096 && warmup>=0 && warmup<=100,"load limits");
        require(!run.empty() && run.size()<=24 && std::isfinite(timeout) && timeout>=.1 && timeout<=30 && std::isfinite(maxSeconds) && maxSeconds>=.1 && maxSeconds<=300,"time or run limits");
        bool single=mode=="offline_single_store" || (mode=="offline_fetch" && cfg.value("source_msgid",13)==5);
        require(!single || recipients==1,"single-chat requires one recipient");require(single || gid>0,"group_id required");
        std::set<int> unique;for(auto& a:accounts){int id=a.at("id"),port=a.at("port");require(id>0 && unique.insert(id).second && port>0 && port<=65535,"invalid account");}
        report["mode"]=mode;report["config"]={{"run_id",run},{"recipients",recipients},{"messages",count},{"message_bytes",bytes},{"warmup",warmup},{"group_id",gid},{"timeout_seconds",timeout},{"max_seconds",maxSeconds}};
        report["clock"]="steady_clock";report["percentile"]="nearest-rank";
        if(mode=="offline_fetch"){
            PhaseScope retrievalPhase("retrieval");
            // Retrieval includes password verification and reconnect cleanup. It is not online delivery latency.
            json fetched={{"expected",recipients*size_t(count)},{"received_unique",0},{"duplicate",0},{"unknown",0},{"batches",json::object()},{"login_attempts",0}};
            std::vector<double> batchMs;auto start=Clock::now();
            try{
                for(size_t i=1;i<accounts.size();++i){auto& a=accounts[i];std::set<int> seen;json sizes=json::array();
                    for(int batch=0;batch<(count+99)/100+1;++batch){
                        require(elapsed(start)<maxSeconds,"retrieval duration limit");json reply;auto loginStart=Clock::now();
                        for(;;){
                            require(!interrupted,"interrupted");require(elapsed(loginStart)<timeout,"relogin cleanup timeout");
                            Conn c=connectTo(a.value("host",std::string("127.0.0.1")),a.at("port"),timeout);c.id=a.at("id");
                            fetched["login_attempts"]=fetched["login_attempts"].get<int>()+1;
                            reply=request(c,{{"msgid",1},{"id",c.id},{"password",a.at("password")}},timeout);
                            if(reply.value("msgid",0)==4 && reply.value("errno",-1)==0)break;
                            require(reply.value("errno",-1)==2,"retrieval login rejected");std::this_thread::sleep_for(std::chrono::milliseconds(10));
                        }
                        batchMs.push_back(elapsed(loginStart)*1000);require(!reply.contains("offline_error"),"offline take failed");
                        require(reply.value("offline_batch_limit",0)==100,"unexpected batch limit");auto messages=reply.at("offlinemsg");require(messages.is_array() && messages.size()<=100,"invalid batch");sizes.push_back(messages.size());
                        for(auto& msg:messages){
                            int seq=-1;std::string body=msg.value("msg",std::string());
                            seq=sequenceFor(body,run,"store",count,bytes);
                            bool valid=seq>=0 && msg.value("msgid",0)==(single?5:13) && msg.value("id",0)==accounts[0]["id"].get<int>() && msg.value("toid",0)==a["id"].get<int>() && (single || msg.value("groupid",0)==gid);
                            if(!valid)fetched["unknown"]=fetched["unknown"].get<int>()+1;
                            else if(!seen.insert(seq).second)fetched["duplicate"]=fetched["duplicate"].get<int>()+1;
                            else fetched["received_unique"]=fetched["received_unique"].get<int>()+1;
                        }
                        if(messages.empty())break;
                    }
                    fetched["batches"][std::to_string(a["id"].get<int>())]=sizes;
                    require(!sizes.empty() && sizes.back()==0,"missing final empty batch");
                }
            }catch(const std::exception& e){fetched["error"]=e.what();}
            fetched["seconds"]=elapsed(start);retrievalPhase.finish();fetched["missing"]=fetched["expected"].get<size_t>()-fetched["received_unique"].get<size_t>();fetched["batch_login_latency"]=stats(batchMs);
            fetched["ok"]=!fetched.contains("error") && fetched["missing"]==0 && fetched["duplicate"]==0 && fetched["unknown"]==0;
            report["retrieval"]=fetched;require(fetched["ok"].get<bool>(),"offline retrieval verification failed");
        }else{
            PhaseScope preparation("preparation");std::vector<Conn> cs;auto prep=Clock::now();cs.push_back(loginAccount(accounts[0],timeout));
            if(mode=="group_online")for(size_t i=1;i<accounts.size();++i)cs.push_back(loginAccount(accounts[i],timeout));
            report["preparation_seconds"]=elapsed(prep);preparation.finish();json go=barrier(cfg,cs,timeout);auto routes=go.at("routes").get<std::vector<std::string>>();require(routes.size()==recipients,"recipient route count");
            int local=0,remote=0,offline=0;for(auto& route:routes){if(route=="local")++local;else if(route=="remote")++remote;else if(route=="offline")++offline;else throw std::runtime_error("invalid route");}
            require(mode=="group_online"?offline==0:offline==int(recipients),"mode route mismatch");report["recipient_routes"]=routes;
            auto phase=[&](int requests,const std::string& label){
                PhaseScope observed(label);
                json r={{"requests_sent",0},{"ack_ok",0},{"ack_failed",0},{"received_unique",0},{"duplicate",0},{"unknown",0},{"stored_offline_submitted",0},{"expected_deliveries",mode=="group_online"?recipients*size_t(requests):0},{"expected_stored",mode=="group_online"?0:recipients*size_t(requests)}};
                auto start=Clock::now();std::vector<double> ackMs,deliveryMs;std::map<std::string,std::vector<double>> routeMs;std::vector<std::set<int>> seen(recipients);
                try{
                    for(int seq=0;seq<requests;++seq){require(!interrupted,"interrupted");require(elapsed(start)<maxSeconds,"phase duration limit");
                        std::string body=bodyFor(run,label,seq,bytes);json msg={{"msgid",single?5:13},{"msg",body}};if(single)msg["toid"]=accounts[1]["id"];else msg["groupid"]=gid;
                        auto sent=Clock::now();cs[0].queue(msg);r["requests_sent"]=seq+1;bool ack=false;size_t got=0;
                        while(!ack || (mode=="group_online" && got<recipients)){
                            require(!interrupted,"interrupted");require(elapsed(sent)<timeout,"message timeout");require(elapsed(start)<maxSeconds,"phase duration limit");
                            std::vector<pollfd> pf;for(auto& c:cs)pf.push_back({c.fd,short(POLLIN|(c.out.empty()?0:POLLOUT)),0});
                            int n=poll(pf.data(),pf.size(),20);if(n<0 && errno==EINTR)continue;require(n>=0,"poll failed");
                            for(size_t i=0;i<cs.size();++i){short ev=pf[i].revents;require(!(ev&(POLLERR|POLLNVAL)),"socket error");if(ev&POLLOUT)cs[i].flush();if(ev&(POLLIN|POLLHUP))cs[i].read();
                                json j;while(cs[i].line(j)){
                                    if(i==0 && j.value("msgid",0)==(single?6:14)){
                                        require(!ack,"duplicate ACK");ack=true;
                                        if(j.value("errno",-1)!=0){r["ack_failed"]=r["ack_failed"].get<int>()+1;throw std::runtime_error("business ACK failed");}
                                        if(single)require(j.value("status",std::string())=="stored_offline","single offline ACK mismatch");
                                        else require(j.value("submitted_local",-1)==local && j.value("submitted_remote",-1)==remote && j.value("submitted",-1)==local+remote && j.value("stored_offline",-1)==offline && j.value("failed",-1)==0,"group ACK route/count mismatch");
                                        r["ack_ok"]=r["ack_ok"].get<int>()+1;r["stored_offline_submitted"]=r["stored_offline_submitted"].get<size_t>()+offline;ackMs.push_back(elapsed(sent)*1000);
                                    }else if(i>0 && j.value("msgid",0)==13){
                                        std::string text=j.value("msg",std::string());int observed=-1;
                                        observed=sequenceFor(text,run,label,seq+1,bytes);
                                        bool valid=observed>=0 && j.value("id",0)==cs[0].id && j.value("toid",0)==cs[i].id && j.value("groupid",0)==gid;
                                        if(!valid || (observed!=seq && !seen[i-1].count(observed)))r["unknown"]=r["unknown"].get<int>()+1;
                                        else if(!seen[i-1].insert(observed).second)r["duplicate"]=r["duplicate"].get<int>()+1;
                                        else {++got;r["received_unique"]=r["received_unique"].get<size_t>()+1;double ms=elapsed(sent)*1000;deliveryMs.push_back(ms);routeMs[routes[i-1]].push_back(ms);}
                                    }else r["unknown"]=r["unknown"].get<int>()+1;
                                }
                            }
                        }
                    }
                }catch(const std::exception& e){r["error"]=e.what();}
                double seconds=elapsed(start);observed.finish();r["seconds"]=seconds;r["request_per_second"]=r["ack_ok"].get<double>()/seconds;r["delivery_per_second"]=r["received_unique"].get<double>()/seconds;r["storage_submitted_per_second"]=r["stored_offline_submitted"].get<double>()/seconds;
                r["missing"]=mode=="group_online"?r["requests_sent"].get<size_t>()*recipients-r["received_unique"].get<size_t>():0;
                r["not_sent"]=requests-r["requests_sent"].get<int>();r["ack_latency"]=stats(ackMs);r["delivery_latency"]=stats(deliveryMs);r["by_route"]=json::object();
                for(auto& item:routeMs)r["by_route"][item.first]={{"received_unique",item.second.size()},{"delivery_per_second",item.second.size()/seconds},{"delivery_latency",stats(item.second)}};
                r["ok"]=!r.contains("error") && r["missing"]==0 && r["duplicate"]==0 && r["unknown"]==0 && r["not_sent"]==0;return r;
            };
            if(mode=="group_online" && warmup){report["warmup"]=phase(warmup,"warmup");require(report["warmup"]["ok"].get<bool>(),"warmup failed");}
            report["measurement"]=phase(count,mode=="group_online"?"measure":"store");require(report["measurement"]["ok"].get<bool>(),"measurement failed");
        }
        report["ok"]=true;code=0;
    }catch(const std::exception& e){report["error"]=e.what();}
    if(!monitorError.empty()){report["monitor_error"]=monitorError;report["ok"]=false;code=1;}
    if(!output.empty()){std::ofstream f(output);if(f){f<<report.dump(2)<<'\n';if(!f)code=1;}else {std::cerr<<"report unavailable\n";code=1;}}
    std::cout<<report.dump(2)<<'\n';return code;
}
