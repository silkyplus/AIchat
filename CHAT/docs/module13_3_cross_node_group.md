# 13.3 跨节点群聊

13.2用户验收通过后，本节把群聊接入同一套用户频道路由。无需新建群专用频道，不新增协议编号或数据库表。两个节点须运行本次编译后的相同版本；旧13.2节点只接单聊，会丢弃新的Redis群聊推送，编译不会更新已运行进程。

## 四个远程终端手动验证

节点A：

```bash
cd /home/chaoqiang/software/chat/CHAT
CHAT_SERVER_ID=course-group-node-a CHAT_REDIS_PREFIX=course133:user: CHAT_DB_NAME=chat_module_test CHAT_DB_USER=chat_module_test CHAT_DB_PASSWORD='Module02_test_only!' ./bin/ChatServer 16014
```

节点B：

```bash
cd /home/chaoqiang/software/chat/CHAT
CHAT_SERVER_ID=course-group-node-b CHAT_REDIS_PREFIX=course133:user: CHAT_DB_NAME=chat_module_test CHAT_DB_USER=chat_module_test CHAT_DB_PASSWORD='Module02_test_only!' ./bin/ChatServer 16015
```

客户端A：

```bash
cd /home/chaoqiang/software/chat/CHAT
./bin/ChatClient 127.0.0.1 16014
```

客户端B：

```bash
cd /home/chaoqiang/software/chat/CHAT
./bin/ChatClient 127.0.0.1 16015
```

先确认系统Redis运行，`redis-cli ping`返回PONG。两边各注册新账号并登录，避免与旧服务上仍登录的账号混淆。A选4创建群，记录返回的实际群ID；B选5加入；各选6应看到双方成员与在线状态。

A选7发群消息，应显示在线提交1、离线存储0、失败0，其中本节点0、Redis跨节点1；B无需按回车即可收到。B选7回复，A自动收到。发送者只有处理结果，不收到自己的群消息推送。

B选0退出，等待片刻；A再选7发群消息，应显示在线提交0、离线存储1、失败0。B重新运行同一客户端命令并登录，应看到离线群聊及群ID。

可选第五个客户端C连16014，注册登录后加入同一群。A发群消息时，B/C均在线则显示在线提交2，其中本节点1、Redis跨节点1；若B退出而C在线，则显示在线提交1、离线存储1，其中本节点1、跨节点0。

## 代码设计

- ChatService新增DeliveryResult与routeMessage(userId,message,error)，由单聊和群聊共用路由：本地连接优先直接提交；无本地连接查询用户状态，offline写MySQL，online发布用户Redis频道。发布订阅数>0算Remote，0回退Offline，-1算Failed且不盲目再写离线。
- 群聊仍先通过GroupModel::recipients校验发送者属于群并取其他成员，不能让客户端传入任意收件人列表。对每个目标调用routeMessage，分别累计Local/Remote/Offline/Failed。失败不撤回其他成员已经提交或存储的消息。
- 每个群转发payload增加toid目标用户，保留msgid=13、id发送者、groupid、msg、time。每位收件人的消息分别构造，确保频道与toid一致；toid不是发送者，不是群ID。单聊原字段和状态保持兼容。
- Redis接收处理兼容msgid=5/13，验证频道与toid一致及字段类型范围；群聊要求正整数groupid、发送者与收件人不同。通过13.2已建立的主EventLoop→目标连接EventLoop提交路径发送，断线时尝试MySQL回退。接收端按可信内部Redis消息处理，群成员资格在源节点检查，未增加消息签名或接收端成员快照校验。
- 14号ACK保留submitted总量、stored_offline、failed，新增submitted_local/submitted_remote两个明细。submitted是本地与跨节点的合计。客户端兼容旧ACK；新ACK在成功或部分失败时均显示明细，未知权限错误无统计时只显示错误。
- Redis故障时某远端成员Failed，不影响后续同节点Local与实际离线Offline处理。部分失败提示retry may duplicate messages，不应把整条群消息盲目重发。

## 自动验证

```bash
cd /home/chaoqiang/software/chat/CHAT
python3 tests/cross_node_group_test.py
```

测试使用临时Redis、独立端口/频道前缀、随机账号和群。实际C++客户端覆盖同节点+跨节点+离线混合成员、双向跨节点群聊、统计总数与明细、单次收取不重复/不自回显、权限和伪造发送者拒绝、群列表角色状态、畸形Redis群消息隔离、残留online无订阅者回退、离线群消息按序展示、带在线群成员的节点退出及重启重登恢复，以及Redis关闭后的部分成功/失败统计。结束清理仅测试数据/进程。13.2单聊及12.1至12.4客户端回归通过。

## 仍有的限制

Redis Pub/Sub不是可靠队列，在线提交不等于终端收到或已读。没有客户端确认、消息去重、自动重连或全群事务；登录/断线/发送竞争和关闭节点时在途丢失仍存在。逐成员路由会同步查询MySQL，离线逐条写入，当前不宣称大群高性能。Redis恢复后需要有序重启节点并重登。强杀节点的在线残留仍由同ID重启恢复。
