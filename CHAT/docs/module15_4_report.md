# 15.4 资源采样、重复实验与分析报告

本模块实现阶段对齐的进程资源采样与统一串行实验入口。服务端业务、线程数、密码验证、日志和默认构建配置沿用原版本。结果用于建立可复查基线，不预设优化结论。

## 用户运行

```bash
cd /home/chaoqiang/software/chat/CHAT
cmake -S . -B build
cmake --build build --target ChatBench ChatGroupBench -j2
./deploy/bench/run-suite.sh --profile quick
```

quick先验证工具链：单聊3个拓扑、2收件人的群聊同节点/混合节点、离线单聊/群聊写入和提取，正式配置均重复3次。多数在线阶段很短，报告会标记short_window=true，这是有效提示，不是测试失败。

完成quick后可运行更长的实验：

```bash
./deploy/bench/run-suite.sh --profile full
```

full默认10对单聊、群收件人2和5、128字节，每配置3轮。先独立预试，按实测速度估算目标约5秒的固定请求数，然后以相同参数重复3次；单聊每对至多10000条、总样本至多200000，群每场景至多1000条。不能保证每次都达到5秒，达到上限仍短就如实标记。离线保留101条批次边界，所以单收件人提取仍可能短。

可选择范围和参数：

```bash
./deploy/bench/run-suite.sh --profile full --scenarios single --pairs 10 --bytes 1024
./deploy/bench/run-suite.sh --profile full --scenarios group --recipients 2 5
./deploy/bench/run-suite.sh --profile quick --overhead-check
```

repeats支持3..5。quick固定3对单聊、2收件人群，用于快速验证；pairs和recipients只控制full规模。overhead-check额外执行一组小规模单节点采样关/开对照，角色calibration，不混入正式重复汇总，也不计算精确开销百分比。预试角色pilot同样独立。

无需手工启动后端。Python负责独立服务、账号/群、数据核对、资源采样和汇总，C++执行聊天负载。日常16014/16015/16016继续运行。各实验串行，避免相互争抢资源；仍与日常进程共享VM和MySQL。

## 阶段与采样原理

include/bench/transport.hpp新增可选PhaseScope。仅设置CHAT_BENCH_MONITOR时，通过权限0600的本地Unix socket发送阶段begin/end事件，并等待监控端确认。旧15.1..15.3命令不设置采样变量时保持原有接口。

阶段包括preparation、warmup、measure、store、retrieval；离线取回单独标记retrieval，包含连接、密码验证、批量提取和断开清理重试。准备与预热不算正式聊天CPU。C++事件明确使用CLOCK_MONOTONIC，与Python time.monotonic_ns使用同一VM时钟。

tests/bench_resources.py对阶段边界采集进程快照，然后确认C++继续；另约每200ms周期采样。阶段事件/快照是顺序发生，不是所有进程同时的原子快照；CPU平均值使用各进程自己的实际采样间隔。监控握手和采样也有开销，正式业务计时尽量位于握手之间；报告资源阶段与C++测量时长略有差别。

CPU读取/proc/<pid>/stat的utime+stime，用SC_CLK_TCK换成秒；CPU%=CPU秒增量/采样墙钟秒*100，100%代表一个逻辑核，多线程进程可超过100%。RSS用stat页数与SC_PAGE_SIZE换算MiB；FD来自/proc/<pid>/fd。PID和starttime共同识别进程，退出或复用后不将新进程当成旧进程。

工具与节点、独立Redis、Nginx master/worker分别采样。Nginx还提供同阶段进程合计CPU秒，不能只看master。MySQL通过/proc的comm发现mysqld/mariadbd；它是共享实例，整体开销不等于压测专属开销。无权限/不存在/未发现的字段保留null与原因，不写成0。raw samples保留具体失败原因。

同时记录VM的CPU累计ticks、MemAvailable、负载；阶段摘要计算VM busy和steal占比。steal仅为可见虚拟CPU计数，不等于完整宿主机负载。

short_window的条件是阶段不足1秒或不足3个周期样本。短批次CPU可能因tick粒度显示0，不能说“没有CPU消耗”。RSS/FD及周期CPU峰值均为采样观测峰值，非连续真实峰值；边界附近的短间隔还可能放大CPU计数粒度误差。

## 文件与报告

统一入口：tests/bench_suite.py、deploy/bench/run-suite.sh。

原15.2/15.3编排新增可选CHAT_BENCH_SAMPLE=1；每个C++工具有自己的监控器，异常/中断时先让工具退出并保存事件，再关闭监控器。15.2支持--topologies选择拓扑；15.3支持--counts-file指定各在线群场景的请求数，统一套件用它实现预试后固定参数。

.runtime/bench/suite-<run-id>/包含：

- environment.json：统一参数与采样口径。
- results.json：所有jobs、原始结果和统一观察项；记录每个子实验目录，未把不同单位合并。
- summary.md：每轮计数、吞吐、P95、后端CPU秒、工具CPU%和短窗口标记；按实际参数/路由分组的成功次数、中位数与最小/最大值。
- analysis.md：观测事实、局限、候选解释和下一步验证。
- 每个job日志。子实验仍在各自baseline-/group-目录，保留二进制SHA256、构建摘录、账号归属、原始计数、数据库核对与组件日志；results.json给出对应绝对路径。

各子实验的*-resources.json保存summary、全部事件与周期/边界样本。source result的resources字段嵌入阶段摘要。原始资源文件与suite目录是联合产物，复制或归档时需一并保留子目录，不只复制summary。

在线吞吐单位是recipient_deliveries/s，离线写入是storage_submissions/s，取回是retrieved_records/s（包含登录过程）。在线P95为投递延迟，离线写入P95为ACK延迟，取回P95为批次登录耗时，不能混用。

重复汇总中的P95是“各轮P95的中位数”，不是合并全部消息后的P95；原始每轮P50/P95/P99仍保留。失败轮留在报告，成功轮才参与性能中位数；成功样本延迟不能代表失败请求体验。

## 验证与边界

```bash
python3 tests/bench_resources_test.py
python3 tests/bench_suite_abort_test.py
```

采样测试检查CPU单位（包括超过100%）、RSS/FD、PID复用与退出、FD权限不足、阶段错误、真实忙循环与空闲进程差异。套件中断测试检查停止传播到本次子编排、资源与失败报告保留、临时密码文件和账号清理。强制SIGKILL不保证清理。正常SIGTERM与Ctrl+C会进入清理；不停止系统Redis、MySQL或日常节点。

Nginx场景混合同节点与跨节点路径，其总吞吐不能与纯跨Redis负载作等价比较；双节点总IO线程数也更多。full不同拓扑可能因预试选出不同消息数，报告保留实际配置；不能用这些结果宣称线性扩容或纯代理开销。CPU较低仍可能是同步IO等待，CPU较高也不能确定唯一瓶颈。

当前采用固定消息数、闭环负载，不提供开放环恒定到达率、函数级perf分析或稳定最大并发证明。代码里的线性连接查找、同步DB/Redis与串行群路由只是候选点。模块16需选择有证据支持的单项假设，增加必要的局部计时或对照后再优化，并复用相同基线验证。

## 本次开发自验证

quick完整套件含采样开/关对照通过，27个正式场景观察均成功；full套件15个配置各3轮，共45个正式观察均通过，6个单收件人离线取回短窗口如实标记。full目录：`.runtime/bench/suite-ba4166197c0e/`；quick目录：`.runtime/bench/suite-a6404bb2a202/`。

full单聊测量窗口约4.4..5.1秒。单节点直连中，三轮压测客户端平均CPU的中位数97.64%，后端合计约92.88%（100%=一个逻辑核）；这提示压测端本身值得排查，不能把当前吞吐作为服务端上限。跨Redis场景后端合计CPU约221.16%、客户端约6.77%；这里只说明观察到的资源分布，未据此确定具体函数瓶颈。Nginx场景仍为8对local、2对remote，不能用混合总吞吐计算代理开销。

采样单位/权限/PID/阶段及忙闲测试、15.1收发与负例、15.3协议负例、带采样的套件中断与群编排中断均通过。中断回测修复了结果收集器将*-resources.json误当业务结果的问题，现仅识别<topology>-<数字>.json。修复后单聊quick三轮再次通过。完整套件保留已有15.2的节点归属与15.3数据库快照/取回清空核对。

支持对既有数据重新生成报告，无需重跑实验：

```bash
python3 tests/bench_suite.py --report-only .runtime/bench/suite-ba4166197c0e
```

报告重新生成仅处理该目录已保存的结果，不变更测量数据或启动服务。用户手动验收尚待完成。
