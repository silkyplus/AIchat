#include "chatservice.hpp"
#include "public.hpp"
#include "model/usermodel.hpp"
#include "model/offlinemessagemodel.hpp"
#include "model/friendmodel.hpp"
#include "model/groupmodel.hpp"
#include "security/password.hpp"
#include <muduo/base/Logging.h>
#include <limits>
#include <cstdlib>
using namespace std;
using namespace placeholders;
using namespace muduo;
using namespace muduo::net;

ChatService* ChatService::instance()
{
    static ChatService instance;
    return &instance;
}

//注册消息id和业务处理方法回调操作
ChatService::ChatService()
{
    _msgHandlerMap[LOGIN_MSG] = bind(&ChatService::login, this, _1, _2, _3);
    _msgHandlerMap[REG_MSG]   = bind(&ChatService::reg,   this, _1, _2, _3);
    _msgHandlerMap[ONE_CHAT_MSG] = bind(&ChatService::oneChat, this, _1, _2, _3);
    _msgHandlerMap[GROUP_LIST_MSG] = bind(&ChatService::groupList, this, _1, _2, _3);
    _msgHandlerMap[FRIEND_LIST_MSG] = bind(&ChatService::friendList, this, _1, _2, _3);
    _msgHandlerMap[ADD_FRIEND_MSG] = bind(&ChatService::addFriend, this, _1, _2, _3);
    _msgHandlerMap[CREATE_GROUP_MSG] = bind(&ChatService::createGroup, this, _1, _2, _3);
    _msgHandlerMap[ADD_GROUP_MSG] = bind(&ChatService::addGroup, this, _1, _2, _3);
    _msgHandlerMap[GROUP_CHAT_MSG] = bind(&ChatService::groupChat, this, _1, _2, _3);
}

//处理登录业务
void ChatService::login(const TcpConnectionPtr &conn, const json &js, Timestamp time)
{
    json response = {{"msgid", LOGIN_MSG_ACK}, {"errno", 1}};
    if (!js.contains("id") || !js["id"].is_number_integer() ||
        !js.contains("password") || !js["password"].is_string()) {
        response["errmsg"] = "id must be a positive integer; password must be a string";
        conn->send(response.dump() + "\n"); return;
    }
    // 在转换 int 之前检查范围，避免大整数截断后指向另一个账号。
    if (js["id"] <= 0 || js["id"] > std::numeric_limits<int>::max()) {
        response["errmsg"] = "invalid user id";
        conn->send(response.dump() + "\n"); return;
    }
    const int id = js["id"].get<int>();
    const std::string password = js["password"].get<std::string>();
    if (password.empty() || password.size() > 128 || password.find('\0') != std::string::npos) {
        response["errmsg"] = "invalid password format";
        conn->send(response.dump() + "\n"); return;
    }
    // 同一个连接的回调由所属 EventLoop 顺序执行，不允许切换账号。
    if (findUserId(conn) != -1) {
        response["errno"] = 2;
        response["errmsg"] = "connection already logged in";
        conn->send(response.dump() + "\n"); return;
    }
    bool boundForLogin = false;
    try {
        UserModel model;
        User user;
        if (!model.query(id, user)) {
            response["errmsg"] = "login unavailable";
            LOG_ERROR << "login database error code=" << model.errorCode();
        } else if (user.getId() == -1 || !verifyPassword(password, user.getPassword())) {
            response["errmsg"] = "invalid user id or password";
        } else {
            bool acquired = false;
            if (!model.trySetOnline(id, acquired)) {
                response["errmsg"] = "login unavailable";
                LOG_ERROR << "login database error code=" << model.errorCode();
            } else if (!acquired) {
                response["errno"] = 2;
                response["errmsg"] = "user already online";
            } else {
                bool bound = false;
                try {
                    bound = bindConnection(id, conn);
                } catch (...) {
                    user.setState("offline");
                    if (!model.updateState(user)) LOG_ERROR << "login rollback failed id=" << id;
                    throw;
                }
                if (!bound) {
                    user.setState("offline");
                    if (!model.updateState(user)) LOG_ERROR << "login rollback failed id=" << id;
                    response["errno"] = 2;
                    response["errmsg"] = "connection or user already bound";
                } else {
                    boundForLogin = true;
                    // 先建立本地连接映射，再确认订阅；成功 ACK 之前必须具备路由能力。
                    if (isStopping() || !_redis.subscribe(userChannel(id))) {
                        clientCloseException(conn);
                        conn->send(json({{"msgid", LOGIN_MSG_ACK}, {"errno", 1},
                            {"errmsg", "login unavailable: Redis subscription failed"}}).dump() + "\n");
                        return;
                    }
                    response["errno"] = 0;
                    response["id"] = user.getId();
                    response["name"] = user.getName();
                    FriendModel friendModel;
                    std::vector<FriendInfo> friends;
                    response["friends"] = json::array();
                    if (friendModel.query(id, friends)) {
                        for (const auto& f : friends)
                            response["friends"].push_back({{"id",f.id},{"name",f.name},{"state",f.state}});
                    } else {
                        response["friends_error"] = "friend list unavailable";
                        LOG_ERROR << "friend query failed code=" << friendModel.errorCode();
                    }
                    GroupModel groupModel;
                    std::vector<Group> groups;
                    response["groups"] = json::array();
                    if (groupModel.query(id, groups)) {
                        for (const auto& g : groups) {
                            json members = json::array();
                            for (const auto& m : g.members)
                                members.push_back({{"id",m.id},{"name",m.name},{"state",m.state},{"role",m.role}});
                            response["groups"].push_back({{"id",g.id},{"groupname",g.name},
                                {"groupdesc",g.description},{"role",g.role},{"members",members}});
                        }
                    } else {
                        response["groups_error"] = "group list unavailable";
                        LOG_ERROR << "group query failed code=" << groupModel.errorCode();
                    }
                    OfflineMessageModel offline;
                    std::vector<std::string> messages;
                    response["offlinemsg"] = json::array();
                    if (offline.take(id, messages)) {
                        for (const auto& message : messages)
                            response["offlinemsg"].push_back(json::parse(message));
                        response["offline_batch_limit"] = 100;
                    } else {
                        response["offline_error"] = "offline messages unavailable; retry on next login";
                        LOG_ERROR << "offline retrieval failed user=" << id;
                    }
                    LOG_INFO << "login connection bound user=" << id
                             << " connection=" << conn->name();
                }
            }
        }
    } catch (const std::exception&) {
        if (boundForLogin) clientCloseException(conn);
        response = {{"msgid", LOGIN_MSG_ACK}, {"errno", 1}, {"errmsg", "login unavailable"}};
        LOG_ERROR << "login failed";
    }
    conn->send(response.dump() + "\n");
}

//处理注册业务
void ChatService::reg(const TcpConnectionPtr &conn, const json &js, Timestamp time)
{
    json response = {{"msgid", REG_MSG_ACK}, {"errno", 1}};
    if (!js.contains("name") || !js["name"].is_string() ||
        !js.contains("password") || !js["password"].is_string()) {
        response["errmsg"] = "name and password must be strings";
        conn->send(response.dump() + "\n"); return;
    }
    const std::string name = js["name"].get<std::string>();
    const std::string password = js["password"].get<std::string>();
    // 最多 50 字节（中文也按 UTF-8 字节计），不超过数据库 50 字符限制。
    if (name.empty() || name.size() > 50 || name.find('\0') != std::string::npos ||
        password.empty() || password.size() > 128 || password.find('\0') != std::string::npos) {
        response["errmsg"] = "name: 1-50 bytes; password: 1-128 bytes; no NUL";
        conn->send(response.dump() + "\n"); return;
    }
    try {
        User user;
        user.setName(name);
        user.setPassword(hashPassword(password));
        UserModel model; // 每个请求单独使用，避免共享错误状态与数据库连接。
        if (model.insert(user)) {
            response["errno"] = 0;
            response["id"] = user.getId();
        } else {
            response["errmsg"] = model.errorCode() == 1062 ? "username already exists" : "registration unavailable";
            // 不输出 SQL、请求或密码。
            LOG_ERROR << "registration database error code=" << model.errorCode();
        }
    } catch (const std::exception&) {
        response["errmsg"] = "registration unavailable";
        LOG_ERROR << "registration failed";
    }
    conn->send(response.dump() + "\n");
}

//获取消息对应的处理器
MsgHandler ChatService::getMsgHandler(int msgid)
{
    auto it = _msgHandlerMap.find(msgid);
    if (it == _msgHandlerMap.end())
    {
        //返回默认处理器
        return [=](const TcpConnectionPtr &conn, const json &js, Timestamp time)
        {
            LOG_ERROR << "msgid " << msgid << " can't find handler!";
            conn->send(json({{"msgid", msgid}, {"errno", 1}, {"errmsg", "unknown msgid"}}).dump() + "\n");
        };
    }
    else
    {
        return _msgHandlerMap[msgid];
    }
}

TcpConnectionPtr ChatService::findConnection(int userId)
{
    std::lock_guard<std::mutex> lock(_connMutex);
    auto it = _userConnMap.find(userId);
    return it == _userConnMap.end() ? TcpConnectionPtr{} : it->second;
}

int ChatService::findUserId(const TcpConnectionPtr& conn)
{
    std::lock_guard<std::mutex> lock(_connMutex);
    for (const auto& entry : _userConnMap)
        if (entry.second == conn) return entry.first;
    return -1;
}

bool ChatService::bindConnection(int userId, const TcpConnectionPtr& conn)
{
    std::lock_guard<std::mutex> lock(_connMutex);
    for (const auto& entry : _userConnMap)
        if (entry.second == conn) return false;
    return _userConnMap.emplace(userId, conn).second;
}

void ChatService::clientCloseException(const TcpConnectionPtr& conn)
{
    int userId = -1;
    {
        std::lock_guard<std::mutex> lock(_connMutex);
        for (auto it = _userConnMap.begin(); it != _userConnMap.end(); ++it) {
            if (it->second == conn) {
                userId = it->first;
                _userConnMap.erase(it);
                break; // erase 后迭代器失效，不再使用它。
            }
        }
    } // 在这里释放锁；数据库访问不得持有连接表锁。
    if (userId == -1) return;
    // 保持数据库 online 直到取消旧订阅完成，避免其他节点新登录后又被旧取消操作影响。
    if (!_redis.unsubscribe(userChannel(userId)) && !isStopping())
        LOG_ERROR << "Redis unsubscribe failed user=" << userId;
    try {
        User user;
        user.setId(userId);
        user.setState("offline");
        UserModel model;
        if (!model.updateState(user)) {
            LOG_ERROR << "disconnect database cleanup failed user=" << userId
                      << " code=" << model.errorCode();
            return;
        }
        LOG_INFO << "disconnect cleanup user=" << userId
                 << " connection=" << conn->name() << " state=offline";
    } catch (const std::exception&) {
        LOG_ERROR << "disconnect cleanup exception user=" << userId;
    }
}

void ChatService::oneChat(const TcpConnectionPtr& conn, const json& js, Timestamp time)
{
    json ack = {{"msgid", ONE_CHAT_MSG_ACK}, {"errno", 1}};
    const int senderId = findUserId(conn);
    if (senderId == -1) {
        ack["errmsg"] = "login required";
    } else if (!js.contains("toid") || !js["toid"].is_number_integer() ||
               js["toid"] <= 0 || js["toid"] > std::numeric_limits<int>::max() ||
               !js.contains("msg") || !js["msg"].is_string()) {
        ack["errmsg"] = "positive integer toid and string msg required";
    } else if (js.contains("id") &&
               (!js["id"].is_number_integer() || js["id"] != senderId)) {
        // 兼容带 id 的课程请求，但不能相信客户端声明的发送者。
        ack["errmsg"] = "sender id does not match connection";
    } else {
        const int recipientId = js["toid"].get<int>();
        const std::string message = js["msg"].get<std::string>();
        ack["toid"] = recipientId;
        if (message.empty() || message.size() > 4096 || message.find('\0') != std::string::npos) {
            ack["errmsg"] = "msg must contain 1-4096 bytes and no NUL";
        } else {
            const json forwarded = {{"msgid", ONE_CHAT_MSG}, {"id", senderId},
                {"toid", recipientId}, {"msg", message}, {"time", time.toFormattedString()}};
            std::string error;
            const DeliveryResult result = routeMessage(recipientId, forwarded, error);
            if (result == DeliveryResult::Failed) ack["errmsg"] = error;
            else {
                ack["errno"] = 0;
                ack["status"] = result == DeliveryResult::Local ? "submitted"
                    : result == DeliveryResult::Remote ? "submitted_remote" : "stored_offline";
            }
        }
    }
    // submitted 只表示提交给连接发送接口，不保证收件人已收到。
    conn->send(ack.dump() + "\n");
}

void ChatService::clearConnections()
{
    std::lock_guard<std::mutex> lock(_connMutex);
    _userConnMap.clear();
}

void ChatService::addFriend(const TcpConnectionPtr& conn, const json& js, Timestamp)
{
    json ack = {{"msgid", ADD_FRIEND_MSG_ACK}, {"errno", 1}};
    int userId = findUserId(conn);
    if (userId == -1) {
        ack["errmsg"] = "login required";
    } else if (!js.contains("friendid") || !js["friendid"].is_number_integer() ||
        js["friendid"] <= 0 || js["friendid"] > std::numeric_limits<int>::max()) {
        ack["errmsg"] = "positive integer friendid required";
    } else {
        int friendId = js["friendid"].get<int>();
        ack["friendid"] = friendId;
        if (friendId == userId) {
            ack["errmsg"] = "cannot add yourself";
        } else {
            UserModel users;
            User target;
            if (!users.query(friendId, target)) {
                ack["errmsg"] = "friend lookup unavailable";
            } else if (target.getId() == -1) {
                ack["errmsg"] = "user does not exist";
            } else {
                FriendModel model;
                switch (model.add(userId, friendId)) {
                case AddFriendResult::Success: ack["errno"] = 0; break;
                case AddFriendResult::Duplicate: ack["errmsg"] = "already friends"; break;
                case AddFriendResult::NotFound: ack["errmsg"] = "user does not exist"; break;
                case AddFriendResult::Invalid: ack["errmsg"] = "invalid friendship"; break;
                default:
                    ack["errmsg"] = "add friend unavailable";
                    LOG_ERROR << "friend insert failed code=" << model.errorCode();
                }
            }
        }
    }
    conn->send(ack.dump() + "\n");
}

void ChatService::createGroup(const TcpConnectionPtr& conn, const json& js, Timestamp)
{
    json ack = {{"msgid", CREATE_GROUP_MSG_ACK}, {"errno", 1}};
    const int creatorId = findUserId(conn);
    if (creatorId == -1) {
        ack["errmsg"] = "login required";
    } else if (!js.contains("groupname") || !js["groupname"].is_string() ||
        !js.contains("groupdesc") || !js["groupdesc"].is_string()) {
        ack["errmsg"] = "groupname and groupdesc must be strings";
    } else {
        Group group;
        group.name = js["groupname"].get<std::string>();
        group.description = js["groupdesc"].get<std::string>();
        if (group.name.empty() || group.name.size() > 50 || group.description.size() > 500 ||
            group.name.find('\0') != std::string::npos || group.description.find('\0') != std::string::npos) {
            ack["errmsg"] = "groupname: 1-50 bytes; groupdesc: 0-500 bytes; no NUL";
        } else {
            GroupModel model;
            if (model.create(creatorId, group)) {
                ack["errno"] = 0;
                ack["groupid"] = group.id;
                ack["groupname"] = group.name;
            } else {
                ack["errmsg"] = "create group unavailable";
                LOG_ERROR << "create group failed code=" << model.errorCode();
            }
        }
    }
    conn->send(ack.dump() + "\n");
}

void ChatService::addGroup(const TcpConnectionPtr& conn, const json& js, Timestamp)
{
    json ack = {{"msgid", ADD_GROUP_MSG_ACK}, {"errno", 1}};
    const int userId = findUserId(conn);
    if (userId == -1) {
        ack["errmsg"] = "login required";
    } else if (!js.contains("groupid") || !js["groupid"].is_number_integer() ||
        js["groupid"] <= 0 || js["groupid"] > std::numeric_limits<int>::max()) {
        ack["errmsg"] = "positive integer groupid required";
    } else {
        const int groupId = js["groupid"].get<int>();
        ack["groupid"] = groupId;
        GroupModel model;
        switch (model.join(userId, groupId)) {
        case JoinGroupResult::Success: ack["errno"] = 0; ack["role"] = "normal"; break;
        case JoinGroupResult::Duplicate: ack["errmsg"] = "already a group member"; break;
        case JoinGroupResult::NotFound: ack["errmsg"] = "group or user does not exist"; break;
        case JoinGroupResult::Invalid: ack["errmsg"] = "invalid group id"; break;
        default:
            ack["errmsg"] = "join group unavailable";
            LOG_ERROR << "join group failed code=" << model.errorCode();
        }
    }
    conn->send(ack.dump() + "\n");
}

void ChatService::groupChat(const TcpConnectionPtr& conn, const json& js, Timestamp time)
{
    json ack = {{"msgid", GROUP_CHAT_MSG_ACK}, {"errno", 1}};
    int senderId = findUserId(conn);
    if (senderId == -1) {
        ack["errmsg"] = "login required";
    } else if (!js.contains("groupid") || !js["groupid"].is_number_integer() ||
        js["groupid"] <= 0 || js["groupid"] > std::numeric_limits<int>::max() ||
        !js.contains("msg") || !js["msg"].is_string()) {
        ack["errmsg"] = "positive integer groupid and string msg required";
    } else if (js.contains("id") && (!js["id"].is_number_integer() || js["id"] != senderId)) {
        ack["errmsg"] = "sender id does not match connection";
    } else {
        int groupId = js["groupid"].get<int>();
        std::string message = js["msg"].get<std::string>();
        ack["groupid"] = groupId;
        if (message.empty() || message.size() > 4096 || message.find('\0') != std::string::npos) {
            ack["errmsg"] = "msg must contain 1-4096 bytes and no NUL";
        } else {
            GroupModel model;
            bool isMember = false;
            std::vector<int> recipients;
            if (!model.recipients(senderId, groupId, isMember, recipients)) {
                ack["errmsg"] = "group lookup unavailable";
                LOG_ERROR << "group recipient query failed code=" << model.errorCode();
            } else if (!isMember) {
                ack["errmsg"] = "group does not exist or you are not a member";
            } else {
                const json forwarded = {{"msgid", GROUP_CHAT_MSG}, {"id", senderId},
                    {"groupid", groupId}, {"msg", message}, {"time", time.toFormattedString()}};
                int local = 0, remote = 0, stored = 0, failed = 0;
                for (int userId : recipients) {
                    try {
                        std::string error;
                        switch (routeMessage(userId, forwarded, error)) {
                        case DeliveryResult::Local: ++local; break;
                        case DeliveryResult::Remote: ++remote; break;
                        case DeliveryResult::Offline: ++stored; break;
                        case DeliveryResult::Failed:
                            ++failed;
                            LOG_ERROR << "group recipient routing failed group=" << groupId << " user=" << userId;
                            break;
                        }
                    } catch (const std::exception&) { ++failed; }
                }
                ack["errno"] = failed ? 1 : 0;
                ack["submitted"] = local + remote;
                ack["submitted_local"] = local;
                ack["submitted_remote"] = remote;
                ack["stored_offline"] = stored;
                ack["failed"] = failed;
                if (failed) ack["errmsg"] = "some recipients failed; retry may duplicate messages";
                // 提交发送不等于已送达；群发非全体收件人原子事务。
            }
        }
    }
    conn->send(ack.dump() + "\n");
}

void ChatService::friendList(const TcpConnectionPtr& conn, const json&, Timestamp)
{
    json ack = {{"msgid", FRIEND_LIST_MSG_ACK}, {"errno", 1}};
    const int userId = findUserId(conn); // 以连接身份为准，不接受客户端指定查询其他人。
    if (userId == -1) ack["errmsg"] = "login required";
    else {
        FriendModel model;
        std::vector<FriendInfo> friends;
        if (!model.query(userId, friends)) ack["errmsg"] = "friend list unavailable";
        else {
            ack["errno"] = 0; ack["friends"] = json::array();
            for (const auto& f : friends)
                ack["friends"].push_back({{"id", f.id}, {"name", f.name}, {"state", f.state}});
        }
    }
    conn->send(ack.dump() + "\n");
}

void ChatService::groupList(const TcpConnectionPtr& conn, const json&, Timestamp)
{
    json ack = {{"msgid", GROUP_LIST_MSG_ACK}, {"errno", 1}};
    const int userId = findUserId(conn);
    if (userId == -1) ack["errmsg"] = "login required";
    else {
        GroupModel model;
        std::vector<Group> groups;
        if (!model.query(userId, groups)) ack["errmsg"] = "group list unavailable";
        else {
            ack["errno"] = 0; ack["groups"] = json::array();
            for (const auto& g : groups) {
                json members = json::array();
                for (const auto& m : g.members)
                    members.push_back({{"id",m.id},{"name",m.name},{"state",m.state},{"role",m.role}});
                ack["groups"].push_back({{"id",g.id},{"groupname",g.name},{"groupdesc",g.description},
                    {"role",g.role},{"members",members}});
            }
        }
    }
    conn->send(ack.dump() + "\n");
}

std::string ChatService::userChannel(int userId) const
{
    return _redisPrefix + std::to_string(userId);
}
bool ChatService::initRedis(EventLoop* loop)
{
    const char* host = std::getenv("CHAT_REDIS_HOST");
    const char* password = std::getenv("CHAT_REDIS_PASSWORD");
    const char* prefix = std::getenv("CHAT_REDIS_PREFIX");
    if (prefix) _redisPrefix = prefix;
    if (_redisPrefix.empty() || _redisPrefix.size() > 128) {
        LOG_ERROR << "invalid CHAT_REDIS_PREFIX"; return false;
    }
    int port = 6379;
    try {
        if (const char* text = std::getenv("CHAT_REDIS_PORT")) {
            size_t end; std::string value(text); port = std::stoi(value, &end);
            if (end != value.size() || port < 1 || port > 65535) throw std::invalid_argument("port");
        }
    } catch (...) { LOG_ERROR << "invalid CHAT_REDIS_PORT"; return false; }
    if (!_redis.connect(host ? host : "127.0.0.1", port,
        [this, loop](const std::string& channel, const std::string& payload) {
            // Redis线程不直接做数据库操作，也不等待Muduo工作线程。
            if (!isStopping()) loop->queueInLoop([this, channel, payload] {
                if (!isStopping()) handleRedisMessage(channel, payload);
            });
        }, password ? password : "")) {
        LOG_ERROR << "Redis startup failed: " << _redis.error(); return false;
    }
    LOG_INFO << "Redis routing ready";
    return true;
}
void ChatService::handleRedisMessage(const std::string& channel, const std::string& payload)
{
    try {
        const json message = json::parse(payload);
        if (!message.is_object() || !message.contains("msgid") || !message["msgid"].is_number_integer() ||
            (message["msgid"] != ONE_CHAT_MSG && message["msgid"] != GROUP_CHAT_MSG) ||
            !message.contains("toid") || !message["toid"].is_number_integer() || message["toid"] <= 0 ||
            message["toid"] > std::numeric_limits<int>::max() || !message.contains("id") ||
            !message["id"].is_number_integer() || message["id"] <= 0 || message["id"] > std::numeric_limits<int>::max() ||
            !message.contains("msg") || !message["msg"].is_string() || !message.contains("time") || !message["time"].is_string())
            throw std::invalid_argument("invalid routed message");
        if (message["msgid"] == GROUP_CHAT_MSG &&
            (!message.contains("groupid") || !message["groupid"].is_number_integer() || message["groupid"] <= 0 ||
             message["groupid"] > std::numeric_limits<int>::max() || message["id"] == message["toid"]))
            throw std::invalid_argument("invalid routed group");
        const int userId = message["toid"].get<int>();
        const std::string content = message["msg"].get<std::string>();
        if (channel != userChannel(userId) || content.empty() || content.size() > 4096 || content.find('\0') != std::string::npos)
            throw std::invalid_argument("invalid routed destination");
        auto conn = findConnection(userId);
        if (conn) {
            // 捕获shared_ptr保护连接生命周期，检查/发送在连接所属EventLoop执行。
            conn->getLoop()->queueInLoop([this, conn, userId, payload] {
                if (isStopping()) return;
                if (conn->connected() && findConnection(userId) == conn) conn->send(payload + "\n");
                else {
                    OfflineMessageModel offline;
                    if (!offline.insert(userId, payload)) LOG_ERROR << "Redis delivery fallback failed user=" << userId;
                }
            });
        } else {
            OfflineMessageModel offline;
            if (!offline.insert(userId, payload)) LOG_ERROR << "Redis delivery fallback failed user=" << userId;
        }
    } catch (const std::exception&) { LOG_ERROR << "invalid Redis chat message dropped"; }
}

ChatService::DeliveryResult ChatService::routeMessage(int userId, const json& message, std::string& error)
{
    // 群聊也带每个收件人的toid，使Redis频道和目标校验统一；不信任客户端自报收件人列表。
    json routed = message;
    routed["toid"] = userId;
    const std::string payload = routed.dump();
    auto local = findConnection(userId);
    if (local) {
        local->send(payload + "\n");
        return DeliveryResult::Local;
    }
    UserModel users;
    User target;
    if (!users.query(userId, target)) { error = "recipient lookup unavailable"; return DeliveryResult::Failed; }
    if (target.getId() == -1) { error = "recipient does not exist"; return DeliveryResult::Failed; }
    if (target.getState() == "online") {
        const long long subscribers = _redis.publish(userChannel(userId), payload);
        if (subscribers > 0) return DeliveryResult::Remote;
        if (subscribers < 0) {
            error = "cross-node routing unavailable; delivery uncertain";
            return DeliveryResult::Failed; // 结果不确定时不再盲目写离线。
        }
    }
    OfflineMessageModel offline;
    if (offline.insert(userId, payload)) return DeliveryResult::Offline;
    error = "offline storage unavailable";
    LOG_ERROR << "offline routing storage failed user=" << userId;
    return DeliveryResult::Failed;
}
