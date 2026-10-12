#include "security/password.hpp"
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <stdexcept>
namespace {
std::string hex(const unsigned char* data, size_t n) {
    const char* digits = "0123456789abcdef";
    std::string out;
    for (size_t i=0; i<n; ++i) { out += digits[data[i] >> 4]; out += digits[data[i] & 15]; }
    return out;
}
}
std::string hashPassword(const std::string& password) {
    unsigned char salt[16], derived[32];
    const int iterations = 600000;
    if (RAND_bytes(salt, sizeof(salt)) != 1 ||
        PKCS5_PBKDF2_HMAC(password.data(), static_cast<int>(password.size()),
            salt, sizeof(salt), iterations, EVP_sha256(), sizeof(derived), derived) != 1)
        throw std::runtime_error("password hashing failed");
    return "pbkdf2_sha256$600000$" + hex(salt, sizeof(salt)) + "$" + hex(derived, sizeof(derived));
}

// 只接受本工程当前的存储格式，拒绝损坏记录和异常迭代参数。
bool verifyPassword(const std::string& password, const std::string& stored) {
    const std::string prefix = "pbkdf2_sha256$600000$";
    if (password.empty() || password.size() > 128 ||
        stored.compare(0, prefix.size(), prefix) != 0 ||
        stored.size() != prefix.size() + 32 + 1 + 64 || stored[prefix.size()+32] != '$') return false;
    auto decode = [](const std::string& text, unsigned char* out, size_t n) {
        auto digit = [](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            return -1;
        };
        for (size_t i=0; i<n; ++i) {
            int a=digit(text[i*2]), b=digit(text[i*2+1]);
            if (a<0 || b<0) return false;
            out[i]=static_cast<unsigned char>((a<<4)|b);
        }
        return true;
    };
    unsigned char salt[16], expected[32], derived[32];
    if (!decode(stored.substr(prefix.size(),32),salt,16) ||
        !decode(stored.substr(prefix.size()+33),expected,32)) return false;
    if (PKCS5_PBKDF2_HMAC(password.data(), static_cast<int>(password.size()),
        salt, sizeof(salt), 600000, EVP_sha256(), sizeof(derived), derived) != 1)
        throw std::runtime_error("password verification failed");
    return CRYPTO_memcmp(expected, derived, sizeof(derived)) == 0;
}
