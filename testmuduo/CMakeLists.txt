cmake_minimum_required(VERSION 3.5)
project(main)

#配置编译选项
set(CMAKE_CXX_FLAGS ${CMAKE_CXX_FLAGS} -g)

#配置头文件搜索路径
#include_directories()

#配置库文件搜索路径
#link_directories()

#设置可执行文件的最终存储路径
set(EXECUTABLE_OUTPUT_PATH ${PROJECT_SOURCE_DIR}/bin)

#设置需要编译的源文件列表
set(SRC_LIST muduo_server.cpp)

#表示生成可执行文件，由SRC_LIST所定义的源文件编译而来
#add_executable(server ${SRC_LIST})
add_executable(server muduo_server.cpp)


#把.指定路径下的所有源文件名字放入变量名 SRC_LIST中
#aux_source_directory(. SRC_LIST)

#表示server这个目标程序需要连接muduo_net\muduo_base和pthread这三个库
target_link_libraries(server muduo_net muduo_base pthread)