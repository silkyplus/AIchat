> 本文件记录早期模块的学习过程。当前实现与部署以根目录 README 和 docs/ 为准；数据库操作目前同步执行于业务回调，不存在独立数据库业务线程池。

# CHAT 项目学习笔记

---

## 一、项目概览

基于 muduo 网络库的 C++ 集群聊天服务器，核心技术栈：

```
网络层：muduo（底层 epoll + Reactor 模式）
序列化：nlohmann/json
构建：CMake
```

---

## 二、项目架构：网络层与业务层分离

```
┌──────────── 网络层（muduo 负责）────────────┐
│                                              │
│  EventLoop（epoll 事件循环）                   │
│       │                                       │
│       ↓                                       │
│  有事件 → 调用你注册的回调函数                   │
│                                              │
│  ┌─────────────────────────────────────┐     │
│  │     业务层（你写的代码）              │     │
│  │                                     │     │
│  │  ChatServer：服务器主类               │     │
│  │    ├── onConnection()  连接回调       │     │
│  │    └── onMessage()     消息回调       │     │
│  │              │                       │     │
│  │              ↓                       │     │
│  │  ChatService：业务处理（单例）         │     │
│  │    ├── login()    登录               │     │
│  │    ├── reg()      注册               │     │
│  │    └── getMsgHandler()  消息分发      │     │
│  └─────────────────────────────────────┘     │
└──────────────────────────────────────────────┘
```

| 层 | 负责什么 | 是哪部分代码 |
|----|---------|------------|
| 网络层 | 收发包、管理连接、epoll 等待 | muduo 库内部（`TcpServer`、`EventLoop`） |
| 业务层 | 收到数据后干什么 | `ChatServer` 回调 + `ChatService` 业务函数 |

---

## 三、核心概念

### 3.1 Reactor 模式

```
传统方式：你主动去问每个 fd "你有数据没？" → 低效
Reactor：注册回调 → epoll_wait 等着 → 内核通知 → 回调处理

               epoll_wait（同时等 10000 个 fd）
                    │
            ┌───────┼───────┐
            ↓       ↓       ↓
          fd=3    fd=7    fd=99
         可读了    可写了   新连接
            │       │       │
            ↓       ↓       ↓
       onMessage  onWrite  onConnection
```

### 3.2 EventLoop（事件循环）

muduo 的核心，内部就是一个死循环：

```cpp
while (true) {
    int n = epoll_wait(...);    // 阻塞等待事件
    for (int i = 0; i < n; i++)
        调回调(events[i]);       // 分发事件
}
```

**纪律：EventLoop 主循环里绝对不能阻塞！** 耗时操作丢给线程池。

### 3.3 单例模式（Singleton）

整个程序只存在一个对象实例：

```cpp
class ChatService {
public:
    static ChatService* instance() {
        static ChatService service;  // 只构造一次
        return &service;
    }
private:
    ChatService() {}                 // 构造函数私有，禁止外部 new
};
```

**为什么用单例：** 业务数据（在线用户、消息处理）全局只需要一份，避免多处创建导致数据不一致。

### 3.4 消息分发机制

不用 if-else，用**函数映射表**：

```cpp
// 步骤 1：定义统一的函数类型别名
using MsgHandler = function<void(conn, json, time)>;

// 步骤 2：存储 消息id → 处理函数 的映射
unordered_map<int, MsgHandler> _msgHandlerMap;

// 步骤 3：构造时注册
_msgHandlerMap[LOGIN_MSG] = bind(&ChatService::login, this, _1, _2, _3);
_msgHandlerMap[REG_MSG]   = bind(&ChatService::reg,   this, _1, _2, _3);

// 步骤 4：收到消息时查表调用
auto handler = _msgHandlerMap.find(msgid);
handler(conn, js, time);
```

| 方式 | 加新业务 |
|------|---------|
| if-else | 改 `onMessage`，加 else if |
| 查表分发 | 只加一行注册，`onMessage` 不用动 |

---

## 四、涉及到的 C++ 知识点

### 4.1 `std::function` + `std::bind`

```cpp
// std::function：定义一个"能接收这些参数的可调用对象"
function<void(int, string)> f;

// std::bind：把成员函数和对象绑定，生成可调用对象
bind(&ChatService::login, this, _1, _2, _3);
//                          ↑       ↑
//                    绑定到当前对象  占位符：调用时传入的参数
```

### 4.2 `using` 类型别名

```cpp
using MsgHandler = std::function<void(const TcpConnectionPtr&, const json&, Timestamp)>;
//    ↑别名              ↑原类型
// 之后写 MsgHandler 就等于写那一长串
```

### 4.3 头文件保护

```cpp
#ifndef CHATSERVICE_HPP     // 如果没定义过
#define CHATSERVICE_HPP     // 就定义
// ...头文件内容...
#endif                      // 结束
// 防止同一个头文件被重复 include
```

### 4.4 头文件规范 ⚠️

```cpp
// ✅ .cpp 里：随意用 using namespace
using namespace std;

// ❌ .hpp 里：永远不要用 using namespace
// 会污染所有 include 这个头文件的文件
```

### 4.5 Lambda 表达式

```cpp
// 匿名函数，用作默认处理器
[=](const TcpConnectionPtr &conn, const json &js, Timestamp time) {
    LOG_ERROR << "msgid " << msgid << " not found!";
};
// [=] 表示按值捕获外部变量（这里的 msgid）
```

---

## 五、涉及到的 muduo 知识点

### 5.1 两个核心类

| 类 | 作用 |
|----|------|
| `EventLoop` | 事件循环，封装 epoll |
| `TcpServer` | 服务器框架，管理连接和事件 |

### 5.2 三个回调注册

```cpp
_server.setConnectionCallback(...)  // 连接建立/断开时调用
_server.setMessageCallback(...)     // 收到数据时调用
_server.setThreadNum(4);            // 设置线程数：1个I/O + 3个worker
```

### 5.3 生命周期

```cpp
EventLoop loop;                              // 1. 创建事件循环
TcpServer server(&loop, addr, "name");       // 2. 创建服务器
server.start();                              // 3. 启动
loop.loop();                                 // 4. 进入事件循环（阻塞在这里）
```

### 5.4 Buffer（接收缓冲区）

```cpp
void onMessage(conn, Buffer* buf, time) {
    string msg = buf->retrieveAllAsString();  // 一次性取出全部数据
    // buf->readableBytes()                   // 看看有多少数据可读
}
```

TCP 是字节流，可能分包。Buffer 帮你暂存拼接，只管 `retrieveAllAsString()` 取出完整的。

---

## 六、构建系统（CMake）

### 6.1 三层结构

```
CHAT/CMakeLists.txt             总指挥：全局配置 + 头文件路径
    └── src/CMakeLists.txt       中转：add_subdirectory(server)
        └── src/server/CMakeLists.txt   执行：编译 cpp + 链接库
```

### 6.2 关键指令

| 指令 | 作用 |
|------|------|
| `include_directories(...)` | 添加头文件搜索路径（对应 g++ -I） |
| `aux_source_directory(. VAR)` | 收集当前目录所有 .cpp 文件到变量 |
| `add_executable(name ${SRC})` | 生成可执行文件 |
| `target_link_libraries(name lib1 lib2)` | 链接依赖库（对应 g++ -l） |
| `add_subdirectory(dir)` | 进入子目录，读取那里的 CMakeLists.txt |

### 6.3 编译命令

```bash
mkdir build && cd build
cmake ..       # 生成 Makefile（只改 CMakeLists.txt 时需要重新运行）
make           # 编译（日常改 .cpp 后只需 make）
```

---

## 七、数据流：一条消息的完整旅程

```
1. 客户端 telnet 发送 {"msgid":1, "user":"zs", "pass":"123"}
                            │
2. 网卡收到数据               │
                            ↓
3. epoll 检测到 fd 可读     ─── 网络层（muduo）
                            │
4. EventLoop 调用回调        │
                            ↓
5. ChatServer::onMessage()  ─── 网络→业务 接口
                            │
6. buf->retrieveAllAsString()   取出原始字符串
                            │
7. json::parse(msg)             反序列化
                            │
8. ChatService::instance()      获取业务单例
   ->getMsgHandler(1)           查表：msgid=1 → login 函数
                            │
9. login(conn, js, time)        执行业务逻辑
```

---

## 八、开发流程

```
1. 写代码
2. Ctrl+C 停旧进程（如果正在跑）
3. make          （在 build/ 目录下）
4. ./bin/ChatServer
5. telnet 127.0.0.1 6000 测试
6. Ctrl+] → quit 退出 telnet
```

---

## 九、文件清单

| 文件 | 角色 |
|------|------|
| `CMakeLists.txt` (根) | 项目全局配置 |
| `src/server/main.cpp` | 程序入口：创建 EventLoop + ChatServer |
| `include/server/chatserver.hpp` | 服务器主类声明 |
| `src/server/chatserver.cpp` | 服务器主类实现：注册回调、消息分发 |
| `include/server/chatservice.hpp` | 业务类声明（单例、消息处理器映射） |
| `src/server/chatservice.cpp` | 业务类实现：login、reg、消息分发 |
| `include/server/public.hpp` | 公共定义：消息类型枚举 |
| `thirdparty/json.hpp` | JSON 库（nlohmann） |

---

## 十、关键术语速查

| 术语 | 一句话 |
|------|--------|
| epoll | Linux 高性能 I/O 多路复用，同时监控大量 fd |
| Reactor | 事件驱动模型：注册回调 → 等待通知 → 分发处理 |
| EventLoop | muduo 中的事件循环，底层调 epoll_wait |
| 单例模式 | 全局唯一实例，构造函数私有 + 静态获取方法 |
| Buffer | muduo 的数据缓冲区，处理 TCP 分包粘包 |
| I/O 线程 | 只做网络收发，不阻塞 |
| 业务线程 | 处理耗时操作（查数据库等），不阻塞 I/O |
| 回调 | 事件来了自动调用的函数（onConnection / onMessage） |
| 序列化/反序列化 | 对象 → JSON字符串 / JSON字符串 → 对象 |
