# AIchat — 高性能集群聊天服务器

基于 C++ 的高并发即时通讯系统，从基础网络 I/O 模型逐步演进到 Reactor 架构，并集成 AI 智能消息处理。

---

## 开发路线图

### 方案一：accept + read/write（阻塞式）

- [ ] 单线程阻塞 I/O 模型
- [ ] 一次只能处理一个客户端连接
- [ ] 理解阻塞 I/O 的问题：其他连接必须排队等待
- [ ] 性能基准：1 并发连接

**目标**：建立网络编程最基本认知，理解 `accept` / `read` / `write` 的阻塞特性。

---

### 方案二：accept + fork（多进程）

- [ ] 每连接 fork 一个子进程处理
- [ ] 父进程负责 accept，子进程负责读写
- [ ] 进程间资源隔离，稳定性好
- [ ] 理解 fork 开销大、子进程回收、僵尸进程问题
- [ ] 性能基准：~100 并发连接

**目标**：理解多进程模型及其资源开销，为多线程方案做对比。

---

### 方案三：accept + thread（多线程）

- [ ] 每连接创建一个线程处理
- [ ] 线程共享进程地址空间，切换开销小于进程
- [ ] 理解线程竞争、数据同步、锁的使用
- [ ] 线程池优化：预创建线程，避免频繁创建销毁
- [ ] 性能基准：~1000 并发连接

**目标**：掌握多线程编程，理解线程池在 C10K 场景下的瓶颈。

---

### 方案四：Reactor 模式（one loop per thread）

- [ ] 基于 muduo 网络库实现
- [ ] 理解 `epoll` I/O 多路复用
- [ ] 理解 EventLoop（事件循环）和 Reactor 设计模式
- [ ] mainReactor + subReactor 架构
- [ ] 非阻塞 I/O + ET/LT 触发模式
- [ ] 性能基准：10w+ 并发连接

**核心组件**：
```
TcpServer → EventLoop → epoll_wait → 回调分发
                ↑
        EventLoopThreadPool（线程池，每个线程一个 EventLoop）
```

**目标**：掌握 Reactor 模式，理解 why muduo（one loop per thread 的设计哲学）。

---

### 方案五：多进程 Reactor（one loop per process）

- [ ] 每进程一个 EventLoop + epoll
- [ ] 进程间通过共享内存 / Unix Domain Socket 通信
- [ ] Nginx 反向代理做负载均衡
- [ ] 利用多核 + 进程隔离，兼顾性能与稳定性
- [ ] 性能基准：数十万并发连接

**目标**：理解 Nginx 架构思想在多进程 Reactor 中的应用。

---

### 性能演进对比

| 方案 | 并发连接 | QPS | 延迟(avg) | 核心瓶颈 |
|------|---------|-----|-----------|---------|
| 方案一：阻塞 I/O | 1 | — | — | 阻塞等待 |
| 方案二：多进程 | ~100 | — | — | fork 开销 |
| 方案三：多线程 | ~1,000 | — | — | 线程切换 |
| 方案四：Reactor+线程池 | 100,000+ | — | — | 业务计算 |
| 方案五：多进程Reactor | 500,000+ | — | — | 进程间通信 |

> 性能数据将在每个方案完成后填入。

---

## AI 智能化扩展（计划中）

| 功能 | 说明 | 技术方案 |
|------|------|---------|
| 敏感消息过滤 | 自动检测并拦截垃圾/违规消息 | ONNX Runtime 部署文本分类模型 |
| 群聊消息摘要 | 长时间未看群，生成消息摘要 | 调用 LLM（本地/API） |
| 智能回复建议 | 基于上下文生成快捷回复 | RAG + LLM |
| 异常流量检测 | 监控 QPS/延迟，自动识别异常 | 时序异常检测模型 |
| 智能聊天机器人 | @机器人提问，RAG 检索历史消息回答 | ChromaDB/FAISS + LLM |

---

## 技术栈

| 分类 | 技术 |
|------|------|
| 语言 | C++17 |
| 网络库 | muduo（基于 epoll） |
| 序列化 | nlohmann JSON |
| 数据库 | MySQL（持久化） + Redis（缓存） |
| 负载均衡 | Nginx |
| AI 推理 | ONNX Runtime / llama.cpp |
| 向量存储 | ChromaDB / FAISS |

---

## 快速开始

> 各方案代码将放在对应目录下，逐步开发中。

```bash
# 编译 muduo 服务器示例
g++ -std=c++17 testmuduo/muduo_server.cpp -o muduo_server -lmuduo_net -lmuduo_base -lpthread

# 编译 JSON 测试
g++ -std=c++17 testjson.cpp -o testjson
```

---

## 目录结构（规划）

```
AIchat/
├── 01_blocking/          # 方案一：阻塞 I/O
├── 02_multiprocess/      # 方案二：多进程
├── 03_multithread/       # 方案三：多线程
├── 04_reactor/           # 方案四：Reactor + 线程池
├── 05_multi_reactor/     # 方案五：多进程 Reactor
├── ai/                   # AI 模块（消息过滤、摘要、RAG）
├── common/               # 公共代码（JSON、编解码）
├── testjson.cpp          # JSON 序列化测试
├── testmuduo/            # muduo 入门测试
└── muduo_test/           # muduo 基础示例
```
