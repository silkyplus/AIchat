# 15.1 C++压测工具与正确性验证

本模块新增独立C++11程序ChatBench。它不修改ChatServer协议，不改变当前16016入口；本阶段验证测量工具正确性，尚未执行15.2性能基线或给出容量结论。

## 一条命令验证

```bash
cd /home/chaoqiang/software/chat/CHAT
cmake -S . -B build
cmake --build build --target ChatBench -j2
python3 tests/bench_smoke_test.py
```

Python只负责创建独立Redis/ChatServer、注册4个临时账号、运行真实C++工具和核对报告；测试后正常终止自己的服务并只删除自己的账号。另有模拟协议服务专门验证异常与分帧。不会停止日常16014/16015/16016，也不会清空数据库或系统Redis。

正常输出包括真实服务40条投递通过，以及错误密码、端口不可用、JSON拆分/合并、重复消息、超时、断开、业务ACK失败、未知消息的PASS。异常测试的PASS表示工具正确报告失败，报告ok=false是预期。每次结果保留在.runtime/bench/<随机run-id>/，包括JSON报告、summary.md和服务日志；临时带密码配置以0600创建并及时删除，报告不包含账号密码。

## 工具接口

ChatBench接收一个JSON配置文件：

```json
{
  "host": "127.0.0.1",
  "port": 16014,
  "run_id": "manual151",
  "accounts": [
    {"id": 1001, "password": "your-test-password"},
    {"id": 1002, "password": "your-test-password"}
  ],
  "messages_per_pair": 20,
  "message_bytes": 128,
  "warmup_per_pair": 2,
  "timeout_seconds": 5,
  "max_seconds": 30,
  "report": "/absolute/existing-directory/result.json"
}
```

以上ID仅示意，手动运行需替换成已注册、当前离线且无离线消息的测试用户。accounts按相邻两个账号组成一对：第一个发送，第二个接收；用户ID必须互不重复。不自动注册或读取生产账号。host当前仅支持IPv4。report父目录须存在。执行：`./deploy/bench/run-bench.sh /absolute/path/config.json`，或直接`./bin/ChatBench /absolute/path/config.json`。退出码0表示通过，1表示参数、准备、业务、传输或报告写入失败。配置文件含测试密码，手动使用时应自行限制文件权限并在用后删除。

## 实现流程

src/bench/main.cpp包含RAII连接Conn、准备期request、分阶段收发phase与统计函数stats。src/bench/CMakeLists.txt构建ChatBench，src/CMakeLists.txt接入bench子目录。未修改现有业务或ChatClient。

连接使用非阻塞socket与poll，检查异步connect的SO_ERROR，所有等待受截止时间控制。准备阶段逐个连接并登录，单独记录连接/登录成功次数、准备总耗时与登录延迟；登录包含真实密码验证。为了避免污染测量，发现账号有离线消息即拒绝。

正式阶段单线程poll同时管理所有收发连接，不为每个连接创建线程。每对用户最多一条请求在途，成功ACK和接收端完整消息都到达后才发下一条，是双条件闭环负载。ACK和通知可能先后颠倒，分别记录布尔状态，不能收到ACK就增加实际投递数。

Conn维护in/out字符串和发送偏移。非阻塞send只写部分时保留尾部继续发送，recv读到EAGAIN后返回；接收缓冲按换行切分JSON，支持一次只收到半帧或一次收到多帧。接收缓冲上限4MiB。套接字由析构关闭，发送使用MSG_NOSIGNAL防止断开引发SIGPIPE直接退出。

msg内容为run_id:阶段:用户对编号:序号|填充。完整消息体严格固定message_bytes字节，并核对原发送用户id、接收用户toid和内容。测试使用ASCII填充，避免字符数与UTF-8字节数混淆。预热与正式阶段使用不同标识和独立样本；预热成功才开始正式测试。

每条样本以steady_clock记录发送排队前时间，分别在收到ACK、完整接收通知时计算延迟。它包含本工具排队及发送处理，是客户端观察值。stats保存有限成功样本，排序计算nearest-rank的P50/P95/P99；报告样本数、平均值和最大值，不用服务器time字段做延迟。

## 报告字段

- connections_attempted/connections_ok、login_attempted/login_ok：准备阶段计数；login_latency为成功登录延迟。
- warmup与measurement分开；sent是已排队请求数，ack_ok/ack_failed/ack_missing描述响应，expected是计划投递数。
- received_unique是核对成功的唯一消息数；missing=sent-received_unique，not_sent=expected-sent。失败ACK与缺失可能对应同一请求，不能相加算失败请求。
- duplicate、unknown：观察窗口内重复或不属于当前请求/收件人的消息。窗口到收齐全部ACK和消息或失败截止为止，不承诺检测关闭连接后才到达的异常消息。
- request_wire_bytes包括请求JSON与换行；message_bytes仅指msg正文，两者含义不同。
- ack_latency和delivery_latency分开，单位毫秒；只统计成功样本，须同时看失败数量。
- seconds覆盖正式首条排队到收齐或失败；sending_seconds截至最后一条请求缓冲写入socket接口，drain_seconds是之后剩余等待。写入socket不表示对端已收到。
- delivery_per_second=实际唯一投递数/seconds。错误、断开、超时或限制触发会保留已完成计数及error，退出失败。

## 限制与下一步

初版支持1..500对用户、每对1..10000条正式消息、正式样本总数最多200000、预热每对最多100条、正文64..4096字节。单操作/单消息超时0.1..60秒，每个收发阶段时长上限0.1..600秒。准备阶段每次连接/登录有独立超时，其总时间随账号数增加；max_seconds不限制整个准备阶段。

正常场景停止发送后等到全部预期响应收齐。失败或某条超时则立即结束该阶段、保存部分报告，不继续长时间排空；missing此时表示本次观察截止未收到，不能证明之后永远不会送达。初版未单独区分迟到消息。SIGINT/SIGTERM让工具退出并尽可能保存报告；不支持对外部强制SIGKILL做清理保证。

本阶段只有少量同节点单聊冒烟，没有跨节点拓扑对比、资源采样、重复正式实验、群聊/离线测量或固定到达率压测。闭环负载在服务变慢时会降低发送速率，不能据此声称过载情况下的P99。压测工具与服务端在同一VM共享资源，poll和JSON处理本身也有开销，后续需一并观察。

下一步15.2使用该工具实施单节点、跨Redis、经Nginx三个拓扑的在线单聊基线，核对节点归属并逐步增加并发；15.3加入群聊和离线，15.4整理资源与重复实验报告。
