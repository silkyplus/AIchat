# 高性能集群聊天服务器

基于 Muduo 的 TCP 长连接聊天服务器，采用网络通信、业务逻辑、数据访问分层设计。当前完整实现位于 [`CHAT/`](CHAT/)，通过 Redis 发布订阅完成跨节点消息转发，Nginx stream 提供统一 TCP 入口。

## 已实现

- 注册登录、密码加盐哈希、连接身份绑定、异常断开清理。
- 好友管理、单聊、群创建/加入/群聊、离线消息持久化与分批拉取。
- 节点归属、正常退出清理、同节点重启时恢复遗留在线状态。
- C++ 命令行客户端。
- Redis 跨节点转发，Nginx round_robin / least_conn 策略对比与故障测试。
- C++ 压测工具、正确性核对、CPU/内存/文件描述符采样。
- 有界 MySQL 连接池、事务租约与失败连接清理，并保留关闭连接池的对比路径。

技术栈：C++11、Linux、Muduo、MySQL、Redis、Nginx、nlohmann JSON、CMake、OpenSSL、hiredis。Python 仅用于测试编排、采样与报告；服务器、客户端、负载生成器均为 C++。

## 构建与运行

开发方式可采用 Windows VS Code Remote SSH 连接 Linux 虚拟机；以下命令在 Linux 中执行。需要已安装 Muduo，以及 CMake、C++ 编译器、pkg-config、MySQL 客户端开发库、hiredis、OpenSSL 开发库；集成测试还需要 Python3、mysql CLI、redis-server 和 redis-cli。

```bash
cd CHAT
cmake -S . -B build
cmake --build build -j2
```

首次初始化独立课程测试库，按顺序执行（module09 的 ALTER TABLE 仅执行一次）：

```bash
mysql -u root -p < sql/module02_test.sql
mysql -u root -p < sql/module08_offline.sql
mysql -u root -p < sql/module09_server_id.sql
mysql -u root -p < sql/module10_friend.sql
mysql -u root -p < sql/module11_group.sql
```

SQL 中的账号密码是公开的独立测试夹具，仅用于隔离的本机课程测试库。部署其他环境应另设凭据，通过环境变量传入。

确保本机 Redis 已启动，然后启动服务器：

```bash
export CHAT_DB_NAME=chat_module_test
export CHAT_DB_USER=chat_module_test
read -rsp 'Database password: ' CHAT_DB_PASSWORD; echo
export CHAT_DB_PASSWORD
./bin/ChatServer 16000
```

另一个 Linux 终端运行客户端，按菜单注册、登录：

```bash
cd CHAT
./bin/ChatClient 127.0.0.1 16000
```

服务器使用 SIGINT/SIGTERM 正常退出。完整集群启停与 Nginx 依赖设置见 [Nginx 入口](CHAT/docs/module14_1_nginx_entry.md)及[节点故障恢复](CHAT/docs/module14_2_failover.md)。默认绑定本机回环地址，Windows 开发时在 VS Code 远程终端中运行客户端。

## 性能验证

同一虚拟机，10 对用户、128 字节消息、跨节点单聊，每轮20,000条，连接池关闭/开启各3轮：

| 指标，中位数 | 关闭连接池 | 开启连接池 |
| --- | --- | --- |
| 收件人投递次数/秒 | 575.03 | 8,246.17 |
| 各轮投递P95的中位数 ms | 26.628 | 2.013 |

该配置下吞吐约14.34倍；六轮120,000条消息的ACK和收件数量核对通过，无缺失或重复。数据是有限闭环工作量的观察，不是最大容量或生产环境承诺。复现命令：

```bash
cd CHAT
python3 tests/db_pool_comparison.py --long
```

详见[连接池设计与测试](CHAT/docs/module16_db_pool.md)、[性能采样与报告](CHAT/docs/module15_4_report.md)。运行时原始数据输出到 `.runtime/`，不纳入源码仓库。

## 目录与文档

- `CHAT/src/`、`CHAT/include/`：服务器、客户端、压测工具源码。
- `CHAT/sql/`：数据库初始化与增量结构脚本。
- `CHAT/deploy/`：节点、Nginx、压测管理脚本。
- `CHAT/tests/`：数据模型、聊天、集群、故障和压测验证。
- `CHAT/docs/`：模块13～16设计、运行与验证记录。

数据库调用目前仍为同步执行；Redis Pub/Sub 不提供持久化确认，发布成功不等于客户端已收件；节点故障后需要客户端重新连接。当前未实现AI扩展。原先阻塞、多进程、多线程等路线保留在[原开发路线](docs/original-roadmap.md)，其中预估并发数字不代表实测结果，也不表示相应方案已完成。
