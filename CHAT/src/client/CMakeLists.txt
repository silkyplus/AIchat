find_package(Threads REQUIRED)
add_executable(ChatClient main.cpp chatclient.cpp)
target_link_libraries(ChatClient PRIVATE Threads::Threads)
