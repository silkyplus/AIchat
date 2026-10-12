# 14.1 Nginx TCP统一入口

本模块只增加部署配置、启停脚本和集成测试，不修改C++聊天协议。Nginx以stream代理TCP字节，新连接等权轮询到两个ChatServer；同一长连接的后续消息保持原后端。

## 当前环境和部署方式

虚拟机没有系统Nginx，当前会话无法免密sudo。已从Ubuntu发行版仓库下载nginx及libnginx-mod-stream 1.28.3软件包，通过dpkg-deb解包到项目`.runtime/nginx`，作为普通用户运行。不安装系统服务、不修改/etc/nginx、不占用80端口。二进制/软件包/日志/PID由.gitignore排除，不提交GitHub。

配置模板deploy/nginx/nginx.conf.in；管理脚本deploy/nginx/chat-nginx.sh；默认运行目录.runtime/nginx-chat。部署到另一台机器时须重新setup，当前私有二进制仍依赖系统共享库，不能当作跨平台独立可执行文件。

## 运行

当前统一入口127.0.0.1:16016已启动，后端为原有127.0.0.1:16014和16015。两个后端须是已接入13.3的ChatServer，使用不同节点ID、相同MySQL/Redis/频道前缀；系统Redis保持运行。

检查或重新启动入口：

```bash
cd /home/chaoqiang/software/chat/CHAT
./deploy/nginx/chat-nginx.sh status
./deploy/nginx/chat-nginx.sh test
./deploy/nginx/chat-nginx.sh start
```

两个远程终端都运行同一个命令：

```bash
cd /home/chaoqiang/software/chat/CHAT
./bin/ChatClient 127.0.0.1 16016
```

分别注册不同新账号并登录，记下ID；选3互发单聊。若分配到不同节点，发送方提示Redis跨节点转发，接收方自动显示。选4建群、另一边选5加入、选7群聊。某用户选0退出后，再发消息，用户重登应显示离线单聊/群聊。初始连续两条连接等权轮询应进入不同节点，但有其他并发新连接、热重载、节点不可用时不能仅凭打开顺序判断；用数据库server_id确认实际归属。

MySQL可执行：

```sql
SELECT id,name,state,server_id FROM User ORDER BY id;
```

日志：`.runtime/nginx-chat/access.log`记录后端地址、字节量和会话时长；`.runtime/nginx-chat/error.log`记录错误。TCP访问日志通常在会话结束时写出，查看时先退出对应客户端。

## 启停与自定义端口

```bash
./deploy/nginx/chat-nginx.sh reload
./deploy/nginx/chat-nginx.sh stop
./deploy/nginx/chat-nginx.sh quit
```

reload生成并校验配置后通知master重载；已有连接由旧worker继续处理。stop快速关闭本项目代理及其连接，不停止后端ChatServer。quit为优雅退出，长连接未结束时可能持续等待；脚本只发送信号，不保证返回时所有worker已结束。无需sudo，不使用systemctl。

setup下载并私有解包，已经安装时仅显示版本：

```bash
./deploy/nginx/chat-nginx.sh setup
```

可用CHAT_NGINX_PORT、CHAT_NGINX_BACKEND_A_PORT、CHAT_NGINX_BACKEND_B_PORT改变端口；配置默认16016/16014/16015，三者必须是不同的合法端口。start/reload时会重新生成配置，所以使用自定义参数时要在这些命令上保持相同环境。测试使用CHAT_NGINX_RUN_DIR绝对路径隔离PID/日志/配置，普通运行不必设置。

脚本按实际/proc可执行文件和本项目prefix校验PID，拒绝对PID复用后的无关进程发送信号；重复start不启动第二个实例。nginx -t可能创建空PID文件，脚本支持该情况。

## 配置设计和边界

stream中upstream定义两后端，共享zone 64k，单worker便于观察；events worker_connections=1024是Nginx连接容量配置，含代理上游等连接，不能当作已验证1024客户端指标。默认轮询按新TCP连接分配，不按每条JSON消息重新选择。

proxy_connect_timeout=1s；proxy_next_upstream允许建立上游连接失败时尝试其他节点，最多2次/3秒；max_fails=1、fail_timeout=5s是被动失败判定。没有主动健康检查。proxy_timeout=1h是读写间隔空闲超时，不是客户端心跳。已有会话中途后端退出会断开，不能迁移或重放业务消息。

监听127.0.0.1仅用于虚拟机本地与Remote SSH终端验证，没有对外开放。后端通常看到Nginx的源地址，当前认证依赖登录连接，不需要PROXY protocol。高性能数据尚未测量，14.1不宣称吞吐提升；故障场景系统验证、策略比较留待14.2/14.3。

## 自动验证

```bash
python3 tests/nginx_entry_test.py
```

独立临时Redis、两个临时ChatServer、随机账号和群、独立Nginx端口和运行目录。实际ChatClient均通过同一入口注册登录，用MySQL归属确认分配到不同后端；验证双向单聊、群聊、连接归属稳定、离线取回、断线状态清理、JSON分段/合并透传、重载后原连接继续通信、错误端口拒绝且不覆盖原配置、重复start、后端访问日志、私有代理启停与PID复用防护。测试结束只清理自己创建的数据和进程，不停止现有16014/16015节点或公共16016入口。

## 连接后立即断开的排查

2026-10-12用户遇到16016连接成功后立即关闭，实际原因是16014/16015均无监听。Nginx只负责代理，不会自动启动ChatServer；连接16016成功只说明入口可连接，不说明后端可用。error.log记录connect() failed (111: Connection refused)/no live upstreams。

已启动course-group-node-a/16014及course-group-node-b/16015为后台进程，日志位于.runtime/chat-nodes/对应节点.log，沿用course133:user:前缀。随后重载项目Nginx，通过真实ChatClient在16016注册、登录和退出验证通过，随机测试账号已清理。三进程需持续运行，虚拟机重启后需重新启动；当前未设置开机自启。

排查命令：

```bash
ss -ltn '( sport = :16014 or sport = :16015 or sport = :16016 )'
tail -30 .runtime/nginx-chat/error.log
```

若后端停止，先按模块13.3的节点命令各自启动，保持两个终端运行，再运行Nginx start/reload和客户端。Nginx status只检查代理进程，不代表后端健康。
