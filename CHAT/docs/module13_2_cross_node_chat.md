# 13.2 Redis 跨节点单聊

先前13.1已提供Redis基础封装；本节把它接入ChatServer登录、断线及单聊。两个服务节点共享MySQL和Redis，使用不同且稳定的CHAT_SERVER_ID。两个客户端分别直连节点，不需要Nginx。

## 手动验证（四个远程终端）

先确认本机Redis可用：`redis-cli ping`应返回PONG。数据库沿用既有测试库，无表结构变更。

终端1，节点A：

```bash
cd /home/chaoqiang/software/chat/CHAT
CHAT_SERVER_ID=course-node-a CHAT_REDIS_PREFIX=course132:user: CHAT_DB_NAME=chat_module_test CHAT_DB_USER=chat_module_test CHAT_DB_PASSWORD='Module02_test_only!' ./bin/ChatServer 16012
```

终端2，节点B：

```bash
cd /home/chaoqiang/software/chat/CHAT
CHAT_SERVER_ID=course-node-b CHAT_REDIS_PREFIX=course132:user: CHAT_DB_NAME=chat_module_test CHAT_DB_USER=chat_module_test CHAT_DB_PASSWORD='Module02_test_only!' ./bin/ChatServer 16013
```

终端3，客户端A：

```bash
cd /home/chaoqiang/software/chat/CHAT
./bin/ChatClient 127.0.0.1 16012
```

终端4，客户端B：

```bash
cd /home/chaoqiang/software/chat/CHAT
./bin/ChatClient 127.0.0.1 16013
```

1. 各自选2注册不同新账号，记录实际用户ID，再选1登录。
2. A选3，输入B的ID和中文消息。A应显示“消息已提交给Redis跨节点转发”，B自动显示收到单聊。
3. B选3回复A，A应自动收到。无需先加好友。
4. B选0退出，等片刻；A再发，应显示已存离线。B重新运行客户端并登录，应看到离线单聊。
5. 选0退出客户端；节点终端Ctrl+C正常关闭。不要强杀系统Redis或正在使用的旧节点。

可选观察：在MySQL执行`SELECT id,name,state,server_id FROM User ORDER BY id;`，分别登录的用户应归属不同节点；用`redis-cli PUBSUB NUMSUB course132:user:实际ID`查看对应频道订阅数。登录成功后通常1，退出后0。频道名和节点ID不同：频道按接收用户划分，节点ID记录用户连接归属。

## 数据流与关键设计

- main启动时调用ChatService::initRedis，配置CHAT_REDIS_HOST（默认127.0.0.1）、CHAT_REDIS_PORT（6379）、CHAT_REDIS_PASSWORD（空）、CHAT_REDIS_PREFIX（chat:user:）。同一聊天集群节点必须共享数据库、Redis和频道前缀；不同应用/测试库应使用不同前缀，Pub/Sub不会按Redis数据库编号隔离。
- 登录沿用MySQL条件UPDATE抢占在线身份；绑定本地连接后，等待SUBSCRIBE用户频道的ACK，之后才返回登录成功。订阅失败移除连接映射、取消可能订阅、恢复当前节点用户offline/NULL。重复登录不创建新订阅。
- 断线先移除本地连接映射，取消用户频道订阅，再更新数据库offline。旧订阅取消完成前保持online，避免其他节点抢到同一用户身份后被旧操作影响。取消失败会使Redis封装失效并shutdown订阅连接，避免无人读取的旧连接继续被PUBLISH计数。
- 单聊优先查本地连接；本地有则继续直接提交。没有则查MySQL：用户不存在返回失败；offline写OfflineMessage；online向用户频道PUBLISH。返回订阅数>0时ACK status=submitted_remote；=0回退MySQL离线存储；-1返回投递不确定错误，不盲目再存离线导致可能重复。
- Redis接收线程把任务提交到ChatServer主EventLoop，不在回调里直接等待订阅或执行数据库操作。主EventLoop校验单聊payload、频道和toid一致，再交给目标连接所属EventLoop；捕获TcpConnectionPtr保护生命周期。连接线程再次检查连接仍在线且仍属于该用户，否则尝试离线存储。无本地连接时主EventLoop执行离线回退；这些数据库调用仍为同步，不能宣称高性能。
- Redis故障时新登录因无法建立订阅而失败回滚；已登录用户同节点聊天、确实离线用户的MySQL存储仍能工作。跨节点失败明确反馈。没有自动重连，恢复Redis后须有序重启节点并重新登录。
- 退出时主loop还存在、TcpServer worker尚未析构前停止并join Redis线程；worker结束后清理映射和当前节点数据库状态。避免Redis回调向已销毁EventLoop提交任务。Redis封装订阅操作改用thread_local标记识别回调线程，避免停止join时其他线程读取std::thread对象。

## 测试

```bash
cd /home/chaoqiang/software/chat/CHAT
python3 tests/cross_node_chat_test.py
```

使用独立临时Redis、随机频道前缀、独立节点和随机账号。验证两实际C++客户端跨节点双向中文聊天、归属/订阅数、重复登录、无额外离线记录、非法Redis消息隔离、取消订阅、跨节点迁移到同节点、MySQL离线取回、残留online无订阅者回退、伪造发送者拒绝、禁止SUBSCRIBE时登录回滚、节点带在线用户退出的状态隔离、重启重登、Redis中断后远端失败/本地仍发送/离线仍存储/新登录失败，以及Redis不可用拒绝服务器启动。测试临时Redis清理，不修改系统Redis配置或清空已有数据。

## 边界

本节只接单聊，跨节点群聊在13.3处理。Redis Pub/Sub不是持久队列，订阅数不是最终接收确认；登录/断线/发送之间仍有竞争，关机丢弃在途任务也可能导致消息丢失。发布超时可能发生在Redis处理命令后，错误只表示未确认成功，不能安全盲重试。没有消息ID、去重、客户端ACK或恰好一次投递。Redis是可信内部基础设施，能向频道发布合法payload的进程可以注入消息，本阶段未实现消息签名或完整ACL隔离。强杀节点残留online由同节点ID下次启动恢复，不自动修复死亡节点状态。
