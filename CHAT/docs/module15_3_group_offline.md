# 15.3 群聊扇出与离线消息测量

本模块设计并实现独立C++程序ChatGroupBench与隔离运行脚本。在线群聊、离线写入、重登取回分别计时，先核对正确性，再提供当前负载下的观察数据。没有修改服务端业务、消息协议或日常入口。

## 运行

```bash
cd /home/chaoqiang/software/chat/CHAT
cmake -S . -B build
cmake --build build --target ChatBench ChatGroupBench -j2
./deploy/bench/run-group.sh
```

不需要手动启动后端。脚本创建独立Redis、两个ChatServer、临时账号和群；退出时清理自己的进程及数据，保留报告。日常16014/16015/16016保持运行。

默认先2个收件人，再5个收件人，均另加1名发送者：

- 在线同节点：发送者与所有收件人在A；正式50条群请求。
- 在线混合节点：发送者在A，收件人在A/B间交替；正式50条群请求。
- 离线单聊：只向1个离线收件人发送101条，核对数据库后重登提取。
- 离线群聊：向所有离线群成员发送101条群请求，核对每个用户记录后逐个重登提取。

在线预热每个场景3条，独立统计。离线场景不预热，避免给数据库样本混入额外消息。每人101条应提取为[100,1,0]：最后一次空批次用于核对不再重复取回。

自定义：

```bash
./deploy/bench/run-group.sh --recipients 2 5 10 --messages 100
./deploy/bench/run-group.sh --recipients 2 --messages 5 --offline-messages 100 --bytes 1024
```

收件人数1..20，最多5个不同规模；在线及离线请求各1..1000，正文64..4096字节。每条消息超时10秒，每个测量阶段上限60秒，准备期连接登录有独立超时，编排等待工具就绪上限60秒、完成上限150秒；触发限制时记录失败并停止后续实验。单收件人时混合节点场景只有跨节点路径，不会同时包含两种路径。

## 原理与代码

src/bench/group.cpp实现ChatGroupBench；include/bench/transport.hpp从原ChatBench提取共享的非阻塞socket、换行JSON收发、单调时钟、百分位统计和信号处理。ChatBench仍使用同样的接口与逻辑。两种工具不依赖交互菜单，不为每个连接创建线程。

tests/bench_group_test.py只负责隔离服务、真实注册/建群/入群、数据库核对和报告编排。账号与群通过既有JSON接口准备，不绕过业务直接写入成员表；随后查询GroupUser核对实际成员集合，确保群发预期收件人与实际群一致。

工具有四个模式：group_online、offline_single_store、offline_group_store、offline_fetch。账号列表第一个是发送者，其余是收件人；每账号带id/password/port，默认主机127.0.0.1。配置中还包含mode、run_id、group_id、messages、message_bytes、warmup、timeout_seconds、max_seconds、ready_file、start_file、report。推荐通过脚本生成配置，不手工填写生产账号。

在线模式登录所有用户。离线写入只登录发送者，收件人保持离线。ready/start握手位于计时前：Python查询User.state/server_id，核对实际节点或离线状态，再原子写出每个收件人的local/remote/offline分类。工具按分类核对群ACK的submitted_local、submitted_remote、submitted、stored_offline和failed，避免只检查errno就误认全部完成。

## 在线群聊计数

每次只发一条群请求，poll同时读取发送者ACK和所有收件人通知；收到成功ACK且所有收件人均收到后，才发下一条。它是单群、单发送者闭环负载，不是多群并发峰值测试。

例如50条请求、5个收件人：

- 预期成功请求50，ACK50；
- 预期实际唯一投递250；
- 发送者不在收件人集合内，不应收到自己发送的群通知。

msg正文编码run_id、阶段和序号，并固定填充到指定字节长度。按(收件人ID,序号)去重，核对msgid=13、发送者id、toid、groupid及完整msg内容。预热与正式阶段分开。未知、重复、计数不符、错误ACK或超时均令测试失败，保留部分计数。

measurement报告requests_sent、ack_ok/ack_failed、expected_deliveries、received_unique、missing、duplicate、unknown、not_sent；request_per_second是成功ACK对应请求/秒，delivery_per_second是实际唯一收件人投递/秒，不能混用。ACK延迟与实际投递延迟各自给出平均、P50/P95/P99、最大值及样本数。by_route按实际local/remote分类统计投递数与延迟，分类吞吐使用同一整轮时间作分母。

## 离线存储与提取

离线单聊检查status=stored_offline；离线群聊要求ACK中stored_offline等于收件人数、在线提交为0且failed=0。stored_offline_submitted是ACK确认的存储提交次数，不直接当成数据库实存结果；storage_submitted_per_second单独报告。

存储完成后，在任何收件人登录之前，Python逐个读取本次测试用户的OfflineMessage记录：核对数量、收件人、发送者、群ID、消息类型以及完整消息编号集合；检查其他测试账号没有意外记录。数据库检查另计，不包含在写入ACK测量中。结果写入database_snapshot。

随后offline_fetch由C++逐个接收用户登录，解析offlinemsg；保持既有每次最多100条的协议。每批关闭连接后重新连接登录，若正常断开清理尚未完成导致errno=2，则在超时内短暂重试，不手工修改用户状态。直到额外一次空批次。按编号、用户、消息类型、群ID核对完整集合，跨批次重复同样算失败。

retrieval报告expected、received_unique、missing、duplicate、unknown、每用户batches、login_attempts、seconds和batch_login_latency。取回时间包含连接、密码验证、离线提取及断开清理重试，不是原发送时刻到接收时刻的在线延迟。工具不使用数据库created_at或服务器time计算端到端耗时。

取回完成后，Python再检查本次账号的OfflineMessage剩余记录为0，结果写入database_remaining。因此本模块同时验证“ACK提交—数据库实际记录—重登实际取回—数据库清空”，不能只凭ACK宣称交付完成。

## 报告与测试

结果在.runtime/bench/group-<run-id>/：summary.md、results.json、environment.json，各场景JSON及组件日志。环境记录参数、平台、CPU数、FD上限、二进制SHA256、CMake配置摘录、节点ID和端口。ready/go文件不含密码；临时账号配置权限0600，用后删除。查询和清理均限制在本次随机命名账号/群，发生失败或Ctrl+C仍执行finally清理。强制SIGKILL不保证清理。

附加验证命令：

```bash
python3 tests/bench_group_protocol_test.py
python3 tests/bench_group_abort_test.py
```

前者用受控协议服务检查分帧、重复、错误ACK扇出计数、错误收件人、超时、离线跨批次重复和错误群ID。负例PASS表示工具正确报告失败。后者检查中断编排仍保存失败报告并清理自己的群、账号与子进程。

## 边界与后续

本模块在一台VM上以两后端进程、共享测试MySQL和独立Redis运行，日常服务仍可能竞争资源。正文默认ASCII，字节数明确。测试窗口截至预期响应收齐或失败截止，不保证识别窗口结束后才到达的消息。

群聊请求仍是服务端串行遍历收件人，非全体原子事务。错误ACK可能伴随部分成员已收到或部分记录已入库，本工具不会自动重发造成重复。离线登录提取沿用已有事务删除语义，不提供客户端确认后删除或断网不丢的额外保证。

当前只测试在线全员、离线全员，以及独立离线单聊，未增加同一群部分在线/部分离线场景；未新增Nginx群聊性能场景、多群同时压测、持续定速负载、CPU/RSS采样或自动瓶颈归因。15.4再汇总重复测量与资源数据，不能将本模块观察值宣称为最大容量。

## 本次开发验收记录

默认2/5收件人场景全部自验证通过：在线共200条正式群请求、700次唯一投递；离线单聊/群聊累计909条记录经数据库逐条核对，随后909条实际取回，无缺失、重复或未知消息；每收件人批次[100,1,0]，取回后测试记录为0。预热另计。报告：`.runtime/bench/group-ab4ddcc265da/`。

附加1024字节、每收件人恰好100条场景通过，批次[100,0]；受控异常测试、编排中断清理测试、15.1回归与15.2三拓扑小规模回归均通过。用户手动验收尚待完成。这些结果代表工具核对正确性及当前有限负载，不是容量上限。
