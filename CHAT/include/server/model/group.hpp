#pragma once
#include <string>
#include <vector>
struct GroupMember {
    int id;
    std::string name;
    std::string state;
    std::string role;
};
struct Group {
    int id = -1;
    std::string name;
    std::string description;
    std::string role;
    std::vector<GroupMember> members;
};
