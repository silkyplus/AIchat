#ifndef PUBLIC_HPP
#define PUBLIC_HPP

/*
server 和 client的公共文件
*/

enum EnMsgType
{
    LOGIN_MSG = 1,//登录消息
    REG_MSG = 2,//注册消息
    LOGIN_MSG_ACK = 4, // 登录响应，保留现有编号
    REG_MSG_ACK = 3, // 注册响应
    ONE_CHAT_MSG = 5, // 单聊请求及转发消息
    ONE_CHAT_MSG_ACK = 6, // 服务端转发结果
    ADD_FRIEND_MSG = 7, // 添加好友
    ADD_FRIEND_MSG_ACK = 8, // 添加好友结果
    CREATE_GROUP_MSG = 9, // 创建群
    CREATE_GROUP_MSG_ACK = 10, // 创建群结果
    ADD_GROUP_MSG = 11, // 加入群
    ADD_GROUP_MSG_ACK = 12, // 加入群结果
    GROUP_CHAT_MSG = 13, // 群聊请求及转发
    GROUP_CHAT_MSG_ACK = 14, // 群聊处理结果
    FRIEND_LIST_MSG = 15, // 刷新好友列表
    FRIEND_LIST_MSG_ACK = 16,
    GROUP_LIST_MSG = 17, // 刷新当前用户的群和成员列表
    GROUP_LIST_MSG_ACK = 18,
};
#endif