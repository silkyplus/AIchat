# 13.1 Redis 发布订阅基础

本阶段新增 chat_redis 库、RedisDemo 演示程序及 RedisPubSubTest 测试程序，尚未接入 ChatServer 的业务路由。

## 构建与运行

依赖 Redis 服务和 hiredis 开发库。当前虚拟机已具备，Redis 位于127.0.0.1:6379。

```bash
cd /home/chaoqiang/software/chat/CHAT
cmake -S . -B build
cmake --build build -j4
redis-cli ping
```

终端A（先运行，等提示订阅成功）：

```bash
cd /home/chaoqiang/software/chat/CHAT
./bin/RedisDemo subscribe course:13.1
```

终端B：

```bash
cd /home/chaoqiang/software/chat/CHAT
./bin/RedisDemo publish course:13.1 '你好，这是C++发布的消息'
```

B应显示订阅连接数1，A应显示收到消息。可再开一个订阅同频道的终端，两个订阅者都会收到，发布结果显示2。A按回车会等待取消订阅确认，再关闭接收线程和连接。全部订阅者退出后发布返回0，该消息不会保存在频道等待未来订阅者。

环境配置：CHAT_REDIS_HOST默认127.0.0.1、CHAT_REDIS_PORT默认6379、CHAT_REDIS_PASSWORD默认空。密码仅通过环境配置读取，不写日志。当前只支持默认用户密码AUTH，不支持自定义ACL用户名、TLS、自动重连或Redis Cluster。

## API与线程约定

`Redis::connect(host, port, handler, password)`创建两条TCP连接并启动订阅线程。对象不可复制；使用结束析构先停止和join，再释放hiredis context。拥有者负责connect/stop/析构，不得在其他调用仍运行时销毁对象；停止前应协调业务调用结束。

`subscribe(channel)`、`unsubscribe(channel)`等待Redis对应ACK，3秒超时废弃连接；一次只允许一条订阅操作等待。调用线程仅提交命令到共享状态；接收线程通过100ms poll统一操作订阅context，包括发送命令、读取和解析回复。这样不让多个线程同时操作同一hiredis context。发布context由publishMutex串行保护。

`publish(channel,message)`使用hiredis的%b显式提供长度，支持中文、百分号、NUL等二进制数据；频道1-256字节、消息最多64KiB。返回>=0表示Redis返回的订阅连接数，-1失败；不代表业务客户端收到或已读。

回调在订阅接收线程执行，应快速返回，禁止在回调里同步subscribe/unsubscribe/stop或销毁Redis对象，否则会等待自己或破坏对象生命周期。可在回调中把任务转交Muduo线程，13.2再接入。回调抛异常被隔离，但封装不负责业务错误日志，业务回调应自行处理并记录。订阅断线或发布异常将connected变为false，error返回不包含密码的原因；当前不自动重连，需要业务层处理失败。

## 测试

```bash
cd /home/chaoqiang/software/chat/CHAT
python3 tests/redis_integration_test.py
```

Python仅启动临时Redis和编排测试；实际发布订阅均由C++完成。临时Redis关闭持久化，使用独立端口和临时目录，不修改系统Redis、不清空已有键。测试包括订阅ACK、动态多频道、重复订阅不重复投递、取消订阅、取消全部后重订阅、两订阅者扇出、UTF-8/二进制/换行、并发发布、回调异常隔离、空闲退出、断线检测、不可连接及AUTH成功/失败。

## 语义边界

Redis Pub/Sub是实时广播，不存储离线消息。订阅连接断开期间消息可能丢失。PUBLISH返回的连接数量不是客户端确认，也不是可靠消息队列。聊天离线消息仍由MySQL负责；13.2将按实际用户连接状态接入。

参考：[hiredis官方说明](https://github.com/redis/hiredis)、[Redis Pub/Sub官方说明](https://redis.io/docs/latest/develop/pubsub/)。
