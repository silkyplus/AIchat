#pragma once
#include <cstdlib>
#include <string>
inline std::string currentNodeId() {
    const char* value = std::getenv("CHAT_SERVER_ID");
    return value ? value : "node-6000";
}
