#include"json.hpp"
using namespace nlohmann;
#include<iostream>
#include<vector>
#include<map>
#include<string>
using namespace std;
//json序列化示例1
// void func1()
// {
//     json js;
//     js["msg_type"] =2;
//     js["from"] = "zhangsan";
//     js["to"] = "lisi";
//     js["msg"] = "hello, how are you?";
//     string sendBuf = js.dump();//json数据对象=>序列化为json字符串
//     cout<<sendBuf.c_str()<<endl;
// }

// //json序列化示例2
// void func2()
// {
//     json js;
//     js["Id"] = {1,2,3,4,5};
//     js["name"] = "zhangsan";
//     js["msg"]["zhangsan"] = "hello world";
//     js["msg"]["liu shuo"] = "hello china";
//     // js["msg"] = {{"zhangsan","hello world"},{"liu shuo","hello china"}};
//     cout<<js<<endl;
// }
// //示例3
// void func3()
// {   //直接序列化数组
//     json js;
//     vector<int> vec;
//     vec.push_back(1);
//     vec.push_back(2);
//     vec.push_back(5);
//     js["list"] = vec;

//     //直接序列化一个map容器
//     map<int,string> m;
//     m.insert({1,"huang shan"});
//     m.insert({2,"hua shan"});
//     m.insert({3,"tai shan"});
//     js["path"] = m;
//     string sendBuf = js.dump();//json数据对象=>序列化为json字符串
//     cout<<sendBuf.c_str()<<endl;
// }

//json反序列化示例1
string func1()
{
    json js;
    js["msg_type"] =2;
    js["from"] = "zhangsan";
    js["to"] = "lisi";
    js["msg"] = "hello, how are you?";
    string sendBuf = js.dump();//json数据对象=>序列化为json字符串
    return sendBuf;
}


//json反序列化示例2
string func2()
{
    json js;
    js["Id"] = {1,2,3,4,5};
    js["name"] = "zhangsan";
    js["msg"]["zhangsan"] = "hello world";
    js["msg"]["liu shuo"] = "hello china";
    // js["msg"] = {{"zhangsan","hello world"},{"liu shuo","hello china"}};
    return js.dump();
}
//示例3
string func3()
{   //直接序列化数组
    json js;
    vector<int> vec;
    vec.push_back(1);
    vec.push_back(2);
    vec.push_back(5);
    js["list"] = vec;

    //直接序列化一个map容器
    map<int,string> m;
    m.insert({1,"huang shan"});
    m.insert({2,"hua shan"});
    m.insert({3,"tai shan"});
    js["path"] = m;
    string sendBuf = js.dump();//json数据对象=>序列化为json字符串
    return sendBuf;
}
int main()
{
    #if 0
    string recvBuf = func1();
    //数据反序列化 json字符串=》反序列化为 数据对象（看作容器，方便访问）
    json jsbuf = json::parse(recvBuf);
    cout<<jsbuf["msg_type"]<<endl;
    cout<<jsbuf["from"]<<endl;
    cout<<jsbuf["to"]<<endl;
    cout<<jsbuf["msg"]<<endl;
    #endif
    #if 0
    string vecBuf = func2();
    json jsvec = json::parse(vecBuf);
    auto arr = jsvec["Id"];
    cout<<jsvec["Id"]<<endl;
    cout<<arr[0]<<endl;
    cout<<arr[1]<<endl;
    cout<<arr[2]<<endl;
    cout<<arr[3]<<endl;
    cout<<arr[4]<<endl;
    cout<<jsvec["name"]<<endl;
    cout<<jsvec["msg"]["zhangsan"]<<endl;
    cout<<jsvec["msg"]["liu shuo"]<<endl;
    #endif
    #if 1
    string recvBuf = func3();
    json jslist = json::parse(recvBuf);
    vector<int> vec = jslist["list"];
    for(int &v:vec)
    {
        cout<<v<<endl;
    }
    #endif
}