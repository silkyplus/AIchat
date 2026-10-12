# 14.2 节点故障与恢复

14.1统一入口已由用户验收。本节增加安全的后台节点管理脚本和隔离故障验证，不改变C++协议，不停止日常16014/16015/16016服务。

## 功能与实际行为

Nginx配置沿用14.1：连接上游失败时尝试其他节点，max_fails=1、fail_timeout=5s为被动判定。节点离线后，新的TCP连接可以进入存活节点；已经连接到故障节点的会话会断开，不自动迁移，不自动重放业务请求。另一节点上的既有连接继续使用原后端。

正常SIGTERM退出会清理该节点用户状态及Redis订阅，用户重新连接统一入口后可在存活节点登录。离线单聊和群聊由MySQL保存，重登时取回。故障节点恢复后无需重载Nginx，经过被动失败等待窗口并有新连接尝试后可再次参与分配；恢复不表示原客户端自动恢复登录。

SIGKILL不能执行业务清理，数据库可能仍为online、server_id仍指向死亡节点；通过存活节点登录会被拒绝，不能把Nginx入口可用等同账号可用。应以原CHAT_SERVER_ID启动故障节点，让启动恢复清理自身残留；之后重新登录。当前没有分布式租约/自动死亡节点身份回收，不自动修改其他节点状态。

全部后端不可用时Nginx进程仍可监听入口，但ChatClient会发现连接关闭/请求失败；不假报登录成功。恢复后需等待被动失败窗口并重新连接。Nginx本身仍是单入口，未做双机高可用。

## 后台节点管理

新增deploy/chat-nodes.sh，默认管理course-group-node-a/16014和course-group-node-b/16015，PID/日志在.runtime/chat-nodes，默认频道前缀course133:user:。

```bash
cd /home/chaoqiang/software/chat/CHAT
./deploy/chat-nodes.sh status
./deploy/chat-nodes.sh stop b
```

stop发送SIGTERM，等待退出，正常管理不自动使用SIGKILL。停止B会断开B上用户的连接，Nginx与A继续运行；status输出两个节点，即使一个已停止。

启动/重启需要数据库密码环境，脚本不把密码写入配置文件：

```bash
export CHAT_DB_PASSWORD='Module02_test_only!'
./deploy/chat-nodes.sh start b
./deploy/chat-nodes.sh restart b
```

start运行中节点会提示already running，不重复启动。可用a/b/all选择；start/stop/restart/status无第二参数时默认all。restart先正常停止，再使用相同节点ID启动。密码变量继承给后端进程；执行unset不影响已运行子进程。

脚本通过PID存活、可执行文件路径、启动端口、环境中的CHAT_SERVER_ID核对身份，识别僵尸进程/无关PID，不盲目kill PID文件中的号码；后台以nohup运行，关闭终端不退出。flock串行管理操作，后台服务关闭管理锁FD，避免永久占锁。检查端口占用、合法ID/端口、节点ID与端口互异；启动等待进程和监听均成功，数据库错误不能仅凭写了PID文件就宣称启动成功。停止等待超时会报错并留给人工检查，不强杀。

兼容先前手动nohup ./bin/ChatServer端口启动且记录了正确PID的节点。没有正确PID文件的监听会显示UNMANAGED_LISTENER并拒绝随意启动/停止；不会自行扫描并接管无关服务。

配置变量：CHAT_NODE_A_ID/B_ID、CHAT_NODE_A_PORT/B_PORT、CHAT_NODES_RUN_DIR（绝对路径），数据库及Redis变量沿用服务器现有接口。若自定义后端端口，须同步Nginx的CHAT_NGINX_BACKEND_A_PORT/B_PORT，并在后续管理命令保留相同配置。status检查进程身份与端口，不是完整数据库/Redis健康探针。

## 用户验证

优先运行隔离验证，不影响日常节点：

```bash
cd /home/chaoqiang/software/chat/CHAT
python3 tests/nginx_failover_test.py
```

Python只负责编排临时Redis、Nginx和后台节点；实际注册、登录、聊天和断线显示均由C++ ChatClient/ChatServer完成。测试创建自己的端口、频道前缀、节点ID、账号和群，并在结束清理。

手动体验：两个客户端都连16016，注册登录，用MySQL的server_id确认归属。停止B后，B的客户端会断开，A原连接仍可用。重新启动客户端连16016并登录，正常停止后的账号可在A恢复。启动B，等约5秒后建立若干新连接，再检查归属，B可重新参与。查看.runtime/nginx-chat/error.log及节点日志，实际分配以数据库归属为准。不建议为手动体验强杀日常节点，强杀路径已在隔离测试验证。

## 自动覆盖

新增tests/nginx_failover_test.py：后台启动/状态/重复启动、单节点SIGTERM、原会话断开且存活会话继续、3个新账号均进入存活节点、离线单聊/群聊重登恢复、节点自动重新参与分配而无重载、恢复后跨节点聊天、测试节点SIGKILL在线残留与订阅消失、残留账号登录拒绝、无订阅者回退离线、同ID重启恢复及重试登录、无关PID保护、全部后端关闭时入口失败、全部恢复后登录、restart命令、缺密码拒绝、错误数据库密码启动失败。回归14.1统一入口/热重载/聊天与离线功能。

未实现客户端自动重连、会话迁移、强杀节点自动身份回收或自动进程拉起；此处验证并固化现有连接级故障切换及人工恢复流程。
