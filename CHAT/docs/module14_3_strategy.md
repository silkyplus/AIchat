# 14.3 轮询与最少连接策略对比

本模块为 Nginx TCP 入口增加可选择的分配策略。C++ 服务端和协议保持原有接口，策略只决定新 TCP 连接进入哪个节点。同一连接上的后续消息始终沿该连接到达原节点，跨节点聊天仍通过 Redis。

## 原理与实现

- `round_robin`：默认等权轮询，轮流分配新连接，不根据当前连接数选择。
- `least_conn`：选择当前活跃连接数较少的节点，等权且连接数相同时按轮询处理。

模板 `deploy/nginx/nginx.conf.in` 在 upstream 中增加策略标记和指令占位符。`deploy/nginx/chat-nginx.sh` 只接受这两个策略名称，轮询不添加算法指令，最少连接添加 `least_conn;`。先验证参数，再生成临时配置并执行 nginx -t；成功后替换配置。非法策略会拒绝，不覆盖配置或发送重载信号。

`zone chat_nodes 64k` 共享上游运行状态。reload 后原有长连接继续由旧 worker 处理，新连接使用新配置；不会重新分配已经建立的会话。后续重载仍需保留策略环境变量，不设置会恢复默认轮询。其他自定义端口和目录变量也需保持一致。

## 运行验证

```bash
cd /home/chaoqiang/software/chat/CHAT
python3 tests/nginx_strategy_test.py
```

测试启动独立 Redis、两个 C++ ChatServer、私有 Nginx 和真实 C++ ChatClient，使用随机空闲端口、节点身份、频道前缀和账号。Python 只负责操作和检查。测试后清理测试资源，不改变日常 16016 入口。

每种策略使用同样步骤：保持6个已登录连接，确认A/B各3个；关闭B的3个连接，等到Nginx访问日志确认连接结束；再建立并保持3个新连接。通过数据库 server_id 确认节点归属。额外检查跨节点聊天、非法策略拒绝以及重载后既有聊天继续、新客户端可以登录。

本次实测：

| 策略 | 关闭B连接后 A/B | 新增3个连接 A/B | 最终 A/B |
| --- | --- | --- | --- |
| round_robin | 3 / 0 | 2 / 1 | 5 / 1 |
| least_conn | 3 / 0 | 0 / 3 | 3 / 3 |

轮询新增连接的先后顺序可能使2/1变为1/2；验证要求两节点都得到新连接。最少连接在这个场景先补足B。最新结果保存在 `.runtime/nginx-strategy-comparison.json`，每次运行覆盖。

## 手动切换日常入口

```bash
export CHAT_NGINX_STRATEGY=least_conn
./deploy/nginx/chat-nginx.sh reload
# 查看已生成的配置
cat .runtime/nginx-chat/nginx.conf
# 恢复轮询
export CHAT_NGINX_STRATEGY=round_robin
./deploy/nginx/chat-nginx.sh reload
```

先确保两后端运行：`./deploy/chat-nodes.sh status`。手动切换只在你执行以上命令后生效，本次开发没有切换日常入口。不能通过给已建立连接重复发消息来验证策略；必须创建新连接。

最少连接适合连接存活时间不一致的场景，但连接数不是CPU、消息频率、数据库压力或吞吐。此实验是分配行为对比，不是性能压测；不能据此宣称更高QPS或更低延迟。仍无既有会话迁移、主动健康检查或客户端自动重连。
