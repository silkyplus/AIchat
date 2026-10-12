#include "bench/transport.hpp"
struct Sample {Clock::time_point start;bool ack=false,received=false;explicit Sample(Clock::time_point t):start(t){}};
int main(int argc,char** argv){
    signal(SIGINT,stop);signal(SIGTERM,stop);
    json report={{"schema",1},{"ok",false},{"connections_attempted",0},{"connections_ok",0},{"login_attempted",0},{"login_ok",0}};
    std::string output;std::vector<Conn> cs;std::vector<double> loginTimes;
    int exitCode=1;
    try{
        require(argc==2,"Usage: ChatBench config.json");std::ifstream f(argv[1]);require(bool(f),"config unavailable");json cfg;f>>cfg;
        output=cfg.at("report").get<std::string>();
        std::string host=cfg.value("host",std::string("127.0.0.1")),run=cfg.at("run_id").get<std::string>();
        int port=cfg.at("port"),count=cfg.value("messages_per_pair",20),bytes=cfg.value("message_bytes",128),warmup=cfg.value("warmup_per_pair",2);
        double timeout=cfg.value("timeout_seconds",5.0),duration=cfg.value("max_seconds",30.0);
        auto accounts=cfg.at("accounts");size_t pairs=accounts.size()/2;
        require(accounts.is_array() && accounts.size()>=2 && accounts.size()%2==0 && pairs<=500,"accounts must contain 1..500 distinct pairs");
        require(count>0 && count<=10000 && pairs*size_t(count)<=200000 && warmup>=0 && warmup<=100,"sample count limit");
        require(bytes>=64 && bytes<=4096 && run.size()<=24 && !run.empty(),"message size or run_id invalid");
        require(port>0 && port<=65535 && std::isfinite(timeout) && timeout>=.1 && timeout<=60 && std::isfinite(duration) && duration>=.1 && duration<=600,"invalid limits");
        report["config"]={{"host",host},{"port",port},{"pairs",pairs},{"messages_per_pair",count},{"message_bytes",bytes},{"warmup_per_pair",warmup},{"timeout_seconds",timeout},{"max_seconds",duration},{"run_id",run}};
        report["load_model"]="closed loop, one request per sender, ACK and delivery required before next";
        report["clock"]="steady_clock";report["percentile"]="nearest-rank";
        std::vector<size_t> order;for(size_t i=0;i<accounts.size();++i)order.push_back(i);
        if(cfg.contains("connection_order")){order=cfg["connection_order"].get<std::vector<size_t>>();auto sorted=order;std::sort(sorted.begin(),sorted.end());require(sorted.size()==accounts.size(),"connection order size");for(size_t i=0;i<sorted.size();++i)require(sorted[i]==i,"connection order must be permutation");}
        PhaseScope preparation("preparation");std::set<int> unique;auto prep=Clock::now();
        for(size_t index:order){auto& a=accounts[index];int id=a.at("id");require(id>0 && unique.insert(id).second,"duplicate or invalid account");
            report["connections_attempted"]=report["connections_attempted"].get<int>()+1;
            cs.push_back(connectTo(a.value("host",host),a.value("port",port),timeout));cs.back().id=id;
            report["connections_ok"]=cs.size();report["login_attempted"]=report["login_attempted"].get<int>()+1;
            auto t=Clock::now();auto reply=request(cs.back(),{{"msgid",1},{"id",id},{"password",a.at("password")}},timeout);
            require(reply.value("msgid",0)==4 && reply.value("errno",-1)==0,"login rejected");
            require(!reply.contains("offlinemsg") || reply["offlinemsg"].empty(),"accounts must have no offline messages");
            loginTimes.push_back(elapsed(t)*1000);report["login_ok"]=loginTimes.size();
        }report["preparation_seconds"]=elapsed(prep);
        std::vector<Conn> ordered;ordered.reserve(cs.size());
        for(size_t i=0;i<accounts.size();++i){auto pos=std::find(order.begin(),order.end(),i)-order.begin();ordered.push_back(std::move(cs[pos]));}cs.swap(ordered);preparation.finish();
        // Optional orchestration barrier: observe actual DB node ownership while logged in.
        std::vector<std::string> routes(pairs,"unclassified");
        if(cfg.contains("ready_file") || cfg.contains("start_file")){
            std::string ready=cfg.at("ready_file"),go=cfg.at("start_file");
            json online=json::array();for(auto& c:cs)online.push_back(c.id);
            std::string readyTemp=ready+".tmp";std::ofstream readyOut(readyTemp);require(bool(readyOut),"cannot write ready file");readyOut<<json({{"ids",online}}).dump();readyOut.close();require(bool(readyOut),"ready file write failed");require(std::rename(readyTemp.c_str(),ready.c_str())==0,"ready file rename failed");
            auto barrier=Clock::now();std::ifstream startFile;
            while(!interrupted && elapsed(barrier)<timeout){startFile.open(go);if(startFile)break;startFile.clear();std::this_thread::sleep_for(std::chrono::milliseconds(10));}
            require(!interrupted && bool(startFile),"ownership barrier timeout");json startConfig;startFile>>startConfig;
            routes=startConfig.at("routes").get<std::vector<std::string>>();require(routes.size()==pairs,"route count mismatch");
            for(auto& route:routes)require(route=="local" || route=="remote","invalid route classification");
        }
        report["pair_routes"]=routes;
        // One outstanding request per pair. Correlate notification payloads independently of ACKs.
        auto phase=[&](int messages,const std::string& label){
            PhaseScope observed(label);
            json r={{"sent",0},{"ack_ok",0},{"ack_failed",0},{"received_unique",0},{"duplicate",0},{"unknown",0},{"expected",pairs*size_t(messages)},{"missing",0},{"timed_out",false},{"request_wire_bytes",0}};
            std::vector<double> ackMs,deliveryMs;std::map<std::string,std::vector<double>> routeAck,routeDelivery;
            std::map<std::string,size_t> routeSent;std::map<std::string,Sample> samples;
            std::vector<int> next(pairs,0);std::vector<std::string> current(pairs);
            auto start=Clock::now();double sendEnd=0;size_t total=pairs*size_t(messages);bool allSent=false;
            auto launch=[&](size_t p){
                std::string key=run+":"+label+":"+std::to_string(p)+":"+std::to_string(next[p]++);
                require(key.size()+1<=size_t(bytes),"message prefix exceeds size");std::string body=key+"|"+std::string(bytes-key.size()-1,'x');
                json msg={{"msgid",5},{"toid",cs[p*2+1].id},{"msg",body}};
                r["request_wire_bytes"]=r["request_wire_bytes"].get<size_t>()+msg.dump().size()+1;
                samples.emplace(key,Sample{Clock::now()});current[p]=key;cs[p*2].queue(msg);
                r["sent"]=r["sent"].get<size_t>()+1;++routeSent[routes[p]];
                if(r["sent"].get<size_t>()==total)allSent=true;
            };
            try{
                for(size_t p=0;p<pairs;++p)launch(p);
                while(r["ack_ok"].get<size_t>()+r["ack_failed"].get<size_t>()<total || r["received_unique"].get<size_t>()<total){
                    require(!interrupted,"interrupted");require(elapsed(start)<duration,"phase duration limit");
                    for(auto& key:current){auto& item=samples.at(key);if(!item.ack||!item.received)require(elapsed(item.start)<timeout,"message timeout");}
                    std::vector<pollfd> pf;for(auto& c:cs)pf.push_back({c.fd,short(POLLIN|(c.out.empty()?0:POLLOUT)),0});
                    int n=poll(pf.data(),pf.size(),20);if(n<0&&errno==EINTR)continue;require(n>=0,"poll failed");
                    for(size_t i=0;i<cs.size();++i){auto& c=cs[i];short ev=pf[i].revents;
                        require(!(ev&(POLLERR|POLLNVAL)),"socket error");if(ev&POLLOUT)c.flush();if(ev&(POLLIN|POLLHUP))c.read();
                        json j;while(c.line(j)){
                            if(j.value("msgid",0)==6 && i%2==0){
                                auto& sample=samples.at(current[i/2]);require(!sample.ack,"duplicate ACK");sample.ack=true;
                                if(j.value("errno",-1)==0){const std::string status=j.value("status",std::string());
                                    require(routes[i/2]=="unclassified" || status==(routes[i/2]=="local"?"submitted":"submitted_remote"),"ACK route differs from verified ownership");
                                    r["ack_ok"]=r["ack_ok"].get<size_t>()+1;double ms=elapsed(sample.start)*1000;ackMs.push_back(ms);routeAck[routes[i/2]].push_back(ms);}
                                else {r["ack_failed"]=r["ack_failed"].get<size_t>()+1;throw std::runtime_error("chat ACK rejected");}
                            }else if(j.value("msgid",0)==5 && i%2==1){
                                std::string body=j.value("msg",std::string()),key=body.substr(0,body.find('|'));auto it=samples.find(key);
                                if(it==samples.end() || key!=current[i/2] || j.value("id",0)!=cs[i-1].id || j.value("toid",0)!=c.id){
                                    // Old known messages are duplicates even after the next request starts.
                                    if(it!=samples.end() && it->second.received && j.value("id",0)==cs[i-1].id && j.value("toid",0)==c.id)r["duplicate"]=r["duplicate"].get<int>()+1;
                                    else r["unknown"]=r["unknown"].get<int>()+1;
                                }else if(it->second.received)r["duplicate"]=r["duplicate"].get<int>()+1;
                                else {require(body==key+"|"+std::string(bytes-key.size()-1,'x'),"payload mismatch");it->second.received=true;r["received_unique"]=r["received_unique"].get<size_t>()+1;double ms=elapsed(it->second.start)*1000;deliveryMs.push_back(ms);routeDelivery[routes[i/2]].push_back(ms);}
                            }else r["unknown"]=r["unknown"].get<int>()+1;
                        }
                    }
                    if(allSent && sendEnd==0 && std::all_of(cs.begin(),cs.end(),[](const Conn& c){return c.out.empty();}))sendEnd=elapsed(start);
                    for(size_t p=0;p<pairs;++p){auto& s=samples.at(current[p]);if(s.ack && s.received && next[p]<messages)launch(p);}
                }
            // Fail fast on rejection/disconnect/expired deadline; partial counts remain in the report.
            }catch(const std::exception& e){r["error"]=e.what();r["timed_out"]=std::string(e.what()).find("timeout")!=std::string::npos || std::string(e.what()).find("duration")!=std::string::npos;}
            double seconds=elapsed(start);observed.finish();r["seconds"]=seconds;r["sending_seconds"]=sendEnd>0?sendEnd:seconds;r["drain_seconds"]=sendEnd>0?seconds-sendEnd:0;
            r["missing"]=r["sent"].get<size_t>()-r["received_unique"].get<size_t>();r["not_sent"]=total-r["sent"].get<size_t>();r["ack_missing"]=r["sent"].get<size_t>()-r["ack_ok"].get<size_t>()-r["ack_failed"].get<size_t>();
            r["delivery_per_second"]=seconds>0?r["received_unique"].get<double>()/seconds:0;
            r["ack_latency"]=stats(ackMs);r["delivery_latency"]=stats(deliveryMs);
            r["by_route"]=json::object();for(auto& item:routeSent){const auto& route=item.first;
                r["by_route"][route]={{"sent",item.second},{"ack_ok",routeAck[route].size()},{"received_unique",routeDelivery[route].size()},{"delivery_per_second",seconds>0?routeDelivery[route].size()/seconds:0},{"ack_latency",stats(routeAck[route])},{"delivery_latency",stats(routeDelivery[route])}};
            }
            r["ok"]=!r.contains("error") && r["duplicate"]==0 && r["unknown"]==0 && r["missing"]==0;
            return r;
        };
        if(warmup){report["warmup"]=phase(warmup,"warmup");require(report["warmup"]["ok"].get<bool>(),"warmup failed");}
        report["measurement"]=phase(count,"measure");require(report["measurement"]["ok"].get<bool>(),"measurement failed");
        report["ok"]=true;exitCode=0;
    }catch(const std::exception& e){report["error"]=e.what();}
    if(!monitorError.empty()){report["monitor_error"]=monitorError;report["ok"]=false;exitCode=1;}
    report["login_latency"]=stats(loginTimes);cs.clear();
    if(!output.empty()){std::ofstream f(output);if(f){f<<report.dump(2)<<'\n';if(!f){std::cerr<<"Cannot write report\n";exitCode=1;}}
        else {std::cerr<<"Cannot open report\n";exitCode=1;}}
    std::cout<<report.dump(2)<<'\n';return exitCode;
}
