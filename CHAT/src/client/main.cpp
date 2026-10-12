#include "client/chatclient.hpp"
#include "public.hpp"
#include <poll.h>
#include <termios.h>
#include <unistd.h>
#include <cerrno>
#include <iostream>
#include <limits>
#include <string>

using json = nlohmann::json;
namespace {
class HiddenInput {
public:
    explicit HiddenInput(bool hide) {
        if (hide && isatty(STDIN_FILENO) && tcgetattr(STDIN_FILENO, &original_) == 0) {
            termios modified = original_; modified.c_lflag &= ~ECHO;
            changed_ = tcsetattr(STDIN_FILENO, TCSANOW, &modified) == 0;
        }
    }
    ~HiddenInput() { if (changed_) { tcsetattr(STDIN_FILENO, TCSANOW, &original_); std::cout << '\n'; } }
private:
    termios original_{};
    bool changed_ = false;
};
// 先校验并组装完整一行，再输出；坏消息不会留下半截输出或影响后续消息。
void showChatMessage(const json& m, bool offline)
{
    try {
        int type = m.at("msgid").get<int>();
        if (type != ONE_CHAT_MSG && type != GROUP_CHAT_MSG)
            throw std::runtime_error("unknown chat type");
        int sender = m.at("id").get<int>();
        if (sender <= 0) throw std::runtime_error("invalid sender");
        std::string line = "[" + std::string(offline ? "离线" : "")
            + (type == ONE_CHAT_MSG ? "单聊" : "群聊") + "] 用户 " + std::to_string(sender);
        if (type == GROUP_CHAT_MSG) {
            int group = m.at("groupid").get<int>();
            if (group <= 0) throw std::runtime_error("invalid group");
            line += "，群 " + std::to_string(group);
        }
        if (m.contains("time")) line += "，时间 " + m.at("time").get<std::string>() + " UTC";
        line += ": " + m.at("msg").get<std::string>();
        std::cout << '\n' << line << '\n';
    } catch (const std::exception&) {
        std::cout << "\n收到格式异常或暂不支持的" << (offline ? "离线消息" : "聊天通知") << "。\n";
    }
}
void showOfflineMessages(const json& login)
{
    if (login.contains("offline_error")) {
        std::cout << "离线消息读取失败，请退出后重新登录重试。\n";
        return; // 不能把服务端取回失败显示成没有消息。
    }
    if (!login.contains("offlinemsg") || !login["offlinemsg"].is_array()) {
        std::cout << "服务器未返回有效的离线消息列表。\n"; return;
    }
    const auto& messages = login["offlinemsg"];
    if (messages.empty()) { std::cout << "暂无离线消息。\n"; return; }
    std::cout << "本次收到 " << messages.size() << " 条离线消息：\n";
    for (const auto& m : messages) showChatMessage(m, true);
    // 取满一批不代表一定有剩余，提示用“可能”，不自动断开当前会话。
    if (login.contains("offline_batch_limit") && login["offline_batch_limit"].is_number_integer()
        && login["offline_batch_limit"] > 0 && messages.size() >= login["offline_batch_limit"].get<size_t>())
        std::cout << "已达到本次批量上限，可能还有离线消息；退出后重新登录可继续读取。\n";
}
bool showNotifications(ChatClient& client)
{
    auto messages = client.takeNotifications();
    for (const auto& m : messages) showChatMessage(m, false);
    return !messages.empty();
}
// 轮询stdin，连接断开时不用等待用户敲回车；不用getline避免stdio预读干扰poll。
bool input(ChatClient& client, const std::string& prompt, std::string& value, bool password = false, size_t limit = 1024)
{
    std::cout << prompt << std::flush;
    HiddenInput hidden(password);
    value.clear(); bool tooLong = false;
    while (client.connected()) {
        if (showNotifications(client)) std::cout << prompt << (password ? "" : value) << std::flush;
        pollfd fd{STDIN_FILENO, POLLIN, 0};
        int n = poll(&fd, 1, 100);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) return false;
        if (!n) continue;
        char ch; ssize_t count = read(STDIN_FILENO, &ch, 1);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        if (ch == '\n') {
            if (!value.empty() && value.back() == '\r') value.pop_back();
            if (tooLong) { std::cout << "输入过长，请重新输入。\n" << prompt << std::flush; value.clear(); tooLong = false; continue; }
            return true;
        }
        if (value.size() < limit) value += ch; else tooLong = true;
    }
    return false;
}
bool positiveInt(const std::string& text, int& value)
{
    if (text.empty()) return false;
    for (char c : text) if (c < '0' || c > '9') return false;
    try {
        long long parsed = std::stoll(text);
        if (parsed <= 0 || parsed > std::numeric_limits<int>::max()) return false;
        value = static_cast<int>(parsed); return true;
    } catch (...) { return false; }
}
}
int main(int argc, char* argv[])
{
    int port;
    if (argc != 3 || !positiveInt(argv[2], port) || port > 65535) {
        std::cerr << "用法: ChatClient IPv4地址 端口\n"; return 1;
    }
    ChatClient client;
    if (!client.connectTo(argv[1], port)) { std::cerr << "连接失败，请检查服务器地址和端口。\n"; return 1; }
    std::cout << "已连接 " << argv[1] << ':' << port << '\n';
    bool loggedIn = false;
    std::string choice, name, password, idText;
    json currentUser;
    while (client.connected()) {
        if (loggedIn) {
            std::cout << "\n当前用户: " << currentUser.at("name").get<std::string>()
                      << "，ID: " << currentUser.at("id").get<int>()
                      << "\n1. 刷新好友列表\n2. 添加好友\n3. 发送单聊消息\n4. 创建群\n5. 加入群\n6. 刷新群列表\n7. 发送群消息\n0. 退出客户端\n";
            if (!input(client, "请选择: ", choice)) break;
            if (choice == "0") break;
            if (choice != "1" && choice != "2" && choice != "3" && choice != "4" && choice != "5" && choice != "6" && choice != "7") {
                std::cout << "无效选项，请输入0至7。\n"; continue;
            }
            json request, response;
            int expected;
            if (choice == "1") {
                request = {{"msgid", FRIEND_LIST_MSG}}; expected = FRIEND_LIST_MSG_ACK;
            } else if (choice == "6") {
                request = {{"msgid", GROUP_LIST_MSG}}; expected = GROUP_LIST_MSG_ACK;
            } else if (choice == "4") {
                std::string groupName, description;
                if (!input(client, "群名称: ", groupName, false, 50)) break;
                if (groupName.empty() || groupName.find('\0') != std::string::npos) {
                    std::cout << "群名称需为1-50字节且不含空字符。\n"; continue;
                }
                if (!input(client, "群描述（可留空）: ", description, false, 500)) break;
                if (description.find('\0') != std::string::npos) {
                    std::cout << "群描述不能包含空字符。\n"; continue;
                }
                request = {{"msgid", CREATE_GROUP_MSG}, {"groupname", groupName}, {"groupdesc", description}};
                expected = CREATE_GROUP_MSG_ACK;
            } else {
                int target;
                if (!input(client, choice == "2" ? "好友ID: " : (choice == "5" || choice == "7" ? "群ID: " : "接收者ID: "), idText)) break;
                if (!positiveInt(idText, target)) { std::cout << "ID必须是合法正整数。\n"; continue; }
                if (choice == "2") {
                    request = {{"msgid", ADD_FRIEND_MSG}, {"friendid", target}}; expected = ADD_FRIEND_MSG_ACK;
                } else if (choice == "5") {
                    request = {{"msgid", ADD_GROUP_MSG}, {"groupid", target}}; expected = ADD_GROUP_MSG_ACK;
                } else {
                    std::string message;
                    if (!input(client, "消息内容: ", message, false, 4096)) break;
                    if (message.empty() || message.find('\0') != std::string::npos) {
                        std::cout << "消息不能为空或包含空字符。\n"; continue;
                    }
                    if (choice == "7") {
                        request = {{"msgid", GROUP_CHAT_MSG}, {"groupid", target}, {"msg", message}}; expected = GROUP_CHAT_MSG_ACK;
                    } else {
                        request = {{"msgid", ONE_CHAT_MSG}, {"toid", target}, {"msg", message}}; expected = ONE_CHAT_MSG_ACK;
                    }
                }
            }
            if (!client.request(request, expected, response)) {
                std::cout << "请求失败：连接关闭或响应超时。\n"; break;
            }
            try {
                if (choice == "7" && response.contains("submitted") && response.contains("stored_offline") && response.contains("failed")) {
                    std::cout << "群发结果：在线提交 " << response.at("submitted").get<int>()
                              << "，离线存储 " << response.at("stored_offline").get<int>()
                              << "，失败 " << response.at("failed").get<int>() << '\n';
                    if (response.contains("submitted_local") && response.contains("submitted_remote"))
                        std::cout << "其中：本节点 " << response.at("submitted_local").get<int>()
                                  << "，Redis跨节点 " << response.at("submitted_remote").get<int>() << '\n';
                }
                if (response.at("errno").get<int>() != 0)
                    std::cout << "操作失败: " << response.value("errmsg", std::string("未知原因")) << '\n';
                else if (choice == "1") {
                    const auto& friends = response.at("friends");
                    if (!friends.is_array()) throw std::runtime_error("invalid friend list");
                    std::cout << "好友列表（刷新时状态）:\n";
                    if (friends.empty()) std::cout << "暂无好友。\n";
                    for (const auto& f : friends)
                        std::cout << "ID: " << f.at("id").get<int>() << "  " << f.at("name").get<std::string>()
                                  << "  " << f.at("state").get<std::string>() << '\n';
                    currentUser["friends"] = friends;
                } else if (choice == "6") {
                    const auto& groups = response.at("groups");
                    if (!groups.is_array()) throw std::runtime_error("invalid group list");
                    std::cout << "群列表（刷新时状态）:\n";
                    if (groups.empty()) std::cout << "暂未加入任何群。\n";
                    for (const auto& g : groups) {
                        std::cout << "群ID: " << g.at("id").get<int>() << "  " << g.at("groupname").get<std::string>()
                                  << "  我的角色: " << g.at("role").get<std::string>()
                                  << "\n描述: " << g.at("groupdesc").get<std::string>() << '\n';
                        const auto& members = g.at("members");
                        if (!members.is_array()) throw std::runtime_error("invalid members");
                        for (const auto& m : members)
                            std::cout << "  成员ID: " << m.at("id").get<int>() << "  " << m.at("name").get<std::string>()
                                      << "  " << m.at("state").get<std::string>() << "  " << m.at("role").get<std::string>() << '\n';
                    }
                    currentUser["groups"] = groups;
                } else if (choice == "4") {
                    std::cout << "创建群成功！群ID: " << response.at("groupid").get<int>() << "，选择6刷新群列表。\n";
                } else if (choice == "5") std::cout << "加入群成功！选择6刷新群列表。\n";
                else if (choice == "7") std::cout << "群消息处理完成（在线提交不代表已读）。\n";
                else if (choice == "2") std::cout << "添加好友成功！选择1刷新列表。\n";
                else {
                    const std::string status = response.at("status").get<std::string>();
                    std::cout << (status == "stored_offline" ? "消息已存为离线消息。\n"
                        : status == "submitted_remote" ? "消息已提交给Redis跨节点转发。\n"
                        : "消息已提交给在线连接。\n");
                }
            } catch (const std::exception&) { std::cout << "服务器响应格式错误，关闭连接。\n"; break; }
            showNotifications(client);
            continue;
        }
        std::cout << "\n===== 聊天客户端 =====\n1. 登录\n2. 注册\n0. 退出\n";
        if (!input(client, "请选择: ", choice)) break;
        if (choice == "0") break;
        if (choice != "1" && choice != "2") { std::cout << "无效选项，请输入0、1或2。\n"; continue; }
        int id = -1;
        if (choice == "2") {
            if (!input(client, "用户名: ", name)) break;
            if (name.empty() || name.size() > 50 || name.find('\0') != std::string::npos) {
                std::cout << "用户名需为1-50字节且不含空字符。\n"; continue;
            }
        } else {
            if (!input(client, "用户ID: ", idText)) break;
            if (!positiveInt(idText, id)) { std::cout << "用户ID必须是合法正整数。\n"; continue; }
        }
        if (!input(client, "密码: ", password, true)) break;
        if (password.empty() || password.size() > 128 || password.find('\0') != std::string::npos) {
            std::cout << "密码需为1-128字节且不含空字符。\n"; continue;
        }
        json message = choice == "2" ? json{{"msgid",REG_MSG},{"name",name},{"password",password}}
                                      : json{{"msgid",LOGIN_MSG},{"id",id},{"password",password}};
        json response;
        bool ok = client.request(message, choice == "2" ? REG_MSG_ACK : LOGIN_MSG_ACK, response);
        password.clear(); message.erase("password");
        if (!ok) { std::cout << "请求失败：连接关闭或响应超时。\n"; break; }
        try {
            if (response.at("errno").get<int>() != 0) {
                std::cout << "操作失败: " << response.value("errmsg",std::string("未知原因")) << '\n'; continue;
            }
            if (choice == "2") {
                std::cout << "注册成功！用户ID: " << response.at("id").get<int>() << "，请使用该ID登录。\n";
            } else {
                if (!response.at("name").is_string() || !response.at("id").is_number_integer())
                    throw std::runtime_error("invalid login response");
                currentUser = std::move(response); loggedIn = true;
                std::cout << "登录成功！\n";
                showOfflineMessages(currentUser);
            }
        } catch (const std::exception&) { std::cout << "服务器响应格式错误，关闭连接。\n"; break; }
    }
    if (!client.connected()) std::cout << "服务器连接已关闭。\n";
    client.stop();
    std::cout << "客户端已退出。\n";
}
