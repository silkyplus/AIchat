#include "model/offlinemessagemodel.hpp"
#include "db/db.hpp"
#include <memory>
#include <stdexcept>
#include "json.hpp"

bool OfflineMessageModel::insert(int userId, const std::string& message)
{
    MySQL db;
    if (!db.connect() || !db.update("INSERT INTO OfflineMessage(userid,message) VALUES (" +
        std::to_string(userId) + ",'" + db.escape(message) + "')")) {
        error_ = db.error(); return false;
    }
    error_.clear(); return true;
}

bool OfflineMessageModel::take(int userId, std::vector<std::string>& messages)
{
    messages.clear();
    MySQL db;
    if (!db.connect() || !db.update("START TRANSACTION")) { error_ = db.error(); return false; }
    try {
        std::unique_ptr<MYSQL_RES, decltype(&mysql_free_result)> result(
            db.query("SELECT id,message FROM OfflineMessage WHERE userid=" +
                std::to_string(userId) + " ORDER BY id LIMIT 100 FOR UPDATE"), mysql_free_result);
        if (!result) throw std::runtime_error(db.error());
        std::vector<std::string> batch;
        std::string ids;
        while (MYSQL_ROW row = mysql_fetch_row(result.get())) {
            if (!ids.empty()) ids += ",";
            ids += row[0];
            unsigned long* lengths = mysql_fetch_lengths(result.get());
            batch.emplace_back(row[1], lengths[1]);
            // 删除前验证格式，损坏记录保留并回滚，避免登录处理中丢失数据。
            auto parsed = nlohmann::json::parse(batch.back());
            if (!parsed.is_object()) throw std::runtime_error("invalid stored message");
        }
        // 只删除实际读取的 ID，避免误删并发写入的新消息。
        if (!ids.empty() && !db.update("DELETE FROM OfflineMessage WHERE userid=" +
            std::to_string(userId) + " AND id IN (" + ids + ")")) throw std::runtime_error(db.error());
        if (!db.update("COMMIT")) throw std::runtime_error(db.error());
        messages.swap(batch); error_.clear(); return true;
    } catch (const std::exception& e) {
        error_ = e.what(); db.update("ROLLBACK"); return false;
    }
}
