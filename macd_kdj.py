"""
量化策略：MACD + KDJ 扫描
MACD 0 轴判断趋势方向，MACD/KDJ 金叉死叉及 KDJ D 值输出
"""
import os
import pandas as pd
import re
import unicodedata
from datetime import datetime
from tqsdk import TqApi, TqAuth
from tqsdk.ta import MACD, KDJ, CCI, EMA
import pymysql
from dotenv import load_dotenv

load_dotenv()

# 快期账号
TQ_ACCOUNT = os.getenv("TQ_ACCOUNT", "快期模拟")
TQ_PASSWORD = os.getenv("TQ_PASSWORD", "<PASSWORD>")

# MySQL配置
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", 3306)),
    "user": os.getenv("MYSQL_USERNAME", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "cool"),
    "charset": "utf8mb4",
}


# 合约名称映射（从数据库 tianqin_type 表加载）
CONTRACT_NAMES = {}


def fetch_contract_names():
    """从数据库 tianqin_type 表查询合约名称映射（status=1 的正常数据）"""
    conn = get_mysql_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT code, name FROM tianqin_type WHERE status = 1"
            )
            rows = cursor.fetchall()
            return {code: name for code, name in rows}
    except Exception as e:
        print(f"从数据库查询合约名称失败: {e}")
        return {}
    finally:
        conn.close()


def get_contract_name(code):
    """根据合约代码获取合约名称"""
    prefix = remove_numbers_from_contract(code)
    return CONTRACT_NAMES.get(prefix, code)


def get_trend_direction(diff, dea, idx=-1):
    """MACD 在 0 轴上方为多头，0 轴下方为空头"""
    
    curr_diff = float(diff.iloc[idx])
    curr_dea = float(dea.iloc[idx])

    return "多头" if curr_dea >= 0 else "空头"


def get_band_signal(diff, dea, idx=-1):
    if len(diff) < 2:
        return ""

    prev_diff = float(diff.iloc[idx - 1])
    prev_dea = float(dea.iloc[idx - 1])
    
    curr_diff = float(diff.iloc[idx])
    curr_dea = float(dea.iloc[idx])

    if curr_dea >= 0:
        return "主要趋势" if curr_diff >= curr_dea else "次级折返"
    else:
        return "主要趋势" if curr_diff <= curr_dea else "次级折返"
    

def get_cross_signal(fast, slow, idx=-1):
    """判断快慢线金叉或死叉（优先识别最新 K 线上的交叉事件）"""
    if len(fast) < 2:
        return "金叉" if float(fast.iloc[idx]) > float(slow.iloc[idx]) else "死叉"

    prev_fast = float(fast.iloc[idx - 1])
    prev_slow = float(slow.iloc[idx - 1])
    curr_fast = float(fast.iloc[idx])
    curr_slow = float(slow.iloc[idx])

    return "金叉" if curr_fast > curr_slow else "死叉"


def get_trend_direction2(fast, slow, idx=-1):

    curr_fast = float(fast.iloc[idx])
    curr_slow = float(slow.iloc[idx])

    return "多头" if curr_fast > curr_slow else "空头"


def analyze_trend(api, contract_code):
    try:
        # 日线K线数据
        klines = api.get_kline_serial(contract_code, 60 * 60 * 24, 200) 

        macd = MACD(klines, 12, 26, 9)
        kdj = KDJ(klines, 9, 3, 3)
        cci = CCI(klines, 14)

        # 1小时线K线数据
        klines2 = api.get_kline_serial(contract_code, 60 * 60, 500) 
        
        ema60 = EMA(klines2, 60)["ema"]
        ema334 = EMA(klines2, 334)["ema"]
        cci2 = CCI(klines2, 60)

        contract_name = get_contract_name(contract_code)
        current_price = float(klines["close"].iloc[-1])
        trend = get_trend_direction(macd["diff"], macd["dea"])
        band = get_band_signal(macd["diff"], macd["dea"])
        kdj_signal = get_cross_signal(kdj["k"], kdj["d"])
        kdj_value = float(kdj["d"].iloc[-1])
        cci_value = float(cci["cci"].iloc[-1])
        hour_trend = get_trend_direction2(ema60, ema334)
        hour_cci_value = float(cci2["cci"].iloc[-1])

        return {
            "contract_code": contract_code,
            "contract_name": contract_name,
            "price": current_price,
            "trend": trend,
            "band": band,
            "kdj_signal": kdj_signal,
            "kdj_value": round(kdj_value, 2),
            "cci_value": round(cci_value, 2),
            "hour_trend": hour_trend,
            "hour_cci_value": round(hour_cci_value, 2),
            "status": "完成",
        }

    except Exception as e:
        return {
            "contract_code": contract_code,
            "status": "错误",
            "error": str(e),
        }


def remove_numbers_from_contract(code):
    """去掉合约代码中的数字，只保留字母部分"""
    return re.sub(r"\d+", "", code)


def get_mysql_connection():
    """获取 MySQL 数据库连接"""
    return pymysql.connect(**MYSQL_CONFIG)


def save_to_mysql(results):
    """将分析结果保存到 MySQL tianqin_data 表"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:00")
    conn = get_mysql_connection()
    try:
        with conn.cursor() as cursor:
            # 先按 code 清空旧数据（可选：每次扫描覆盖）
            # cursor.execute("DELETE FROM tianqin_data")

            sql = """
                INSERT INTO tianqin_data
                    (createTime, updateTime, code, name, price, trend, band,
                     kdjSignal, kdjValue, cciValue, hourTrend, hourCciValue)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            values = []
            for r in results:
                values.append((
                    now, now,
                    r.get("contract_code", ""),
                    r.get("contract_name", ""),
                    round(float(r.get("price", 0)), 2),
                    r.get("trend", ""),
                    r.get("band", ""),
                    r.get("kdj_signal", ""),
                    round(float(r.get("kdj_value", 0)), 2),
                    round(float(r.get("cci_value", 0)), 2),
                    r.get("hour_trend", ""),
                    round(float(r.get("hour_cci_value", 0)), 2),
                ))
            cursor.executemany(sql, values)
        conn.commit()
        print(f"  MySQL 保存成功: {len(results)} 条记录 -> tianqin_data")
    except Exception as e:
        conn.rollback()
        print(f"  MySQL 保存失败: {e}")
    finally:
        conn.close()


def _display_width(text):
    """字符串在等宽终端中的显示宽度（全角/宽字符计为2）"""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        for ch in str(text)
    )


def _pad_text(text, width, align="left"):
    text = str(text)
    padding = width - _display_width(text)
    if padding <= 0:
        return text
    if align == "right":
        return " " * padding + text
    return text + " " * padding


def print_aligned_table(df, col_align=None, sep="  "):
    """打印列对齐的表格（兼容中文等宽字符）"""
    if col_align is None:
        col_align = {}
    columns = list(df.columns)
    data = [list(row) for row in df.itertuples(index=False, name=None)]

    widths = []
    for i, col in enumerate(columns):
        default_align = "right" if df[col].dtype.kind in "iufc" else "left"
        align = col_align.get(col, default_align)
        max_w = _display_width(col)
        for row in data:
            max_w = max(max_w, _display_width(row[i]))
        widths.append((max_w, align))

    print(sep.join(_pad_text(col, widths[i][0]) for i, col in enumerate(columns)))
    for row in data:
        print(sep.join(
            _pad_text(row[i], widths[i][0], widths[i][1])
            for i in range(len(columns))
        ))


def find_cont_quotes_by_prefixs(all_cont_quotes, prefixes):
    """根据合约前缀从 all_cont_quotes 中查找匹配的完整合约代码"""
    matched_contracts = []

    if isinstance(all_cont_quotes, dict):
        contract_list = []
        for key, value in all_cont_quotes.items():
            if isinstance(value, str) and "." in value:
                contract_list.append(value)
            elif isinstance(key, str) and "." in key:
                contract_list.append(key)
    elif isinstance(all_cont_quotes, list):
        contract_list = all_cont_quotes
    else:
        try:
            contract_list = list(all_cont_quotes) if hasattr(all_cont_quotes, "__iter__") else []
        except Exception:
            contract_list = []

    for prefix in prefixes:
        prefix_clean = remove_numbers_from_contract(prefix)
        matches = []
        for contract in contract_list:
            if not isinstance(contract, str):
                continue
            contract_clean = remove_numbers_from_contract(contract)
            if contract_clean == prefix_clean:
                matches.append(contract)

        if matches:
            matched_contract = matches[0]
            matched_contracts.append(matched_contract)
            contract_name = get_contract_name(matched_contract)
        else:
            print(f"  ⚠ 警告: 未找到匹配 {prefix} ({CONTRACT_NAMES.get(prefix, '')}) 的合约")

    return matched_contracts


def scan_contracts(api, cont_quotes):
    """扫描多个合约"""
    results = []

    for contract_code in cont_quotes:
        contract_name = get_contract_name(contract_code)
        print(f"正在分析: {contract_code} ({contract_name})")
        result = analyze_trend(api, contract_code)
        results.append(result)

        if result.get("status") == "完成":
            print(
                f"  ✅ 趋势方向: {result['trend']} | 当前运行: {result['band']} | "
                f"kdj_signal: {result['kdj_signal']} | kdj_value: {result['kdj_value']:.2f} | "
                f"cci_value: {result['cci_value']:.2f}"
            )
        elif result.get("status") == "错误":
            print(f"  ❌ 错误: {result.get('error', '未知错误')}")

    return results


def main():
    """主函数"""
    global CONTRACT_NAMES

    # 从数据库加载合约名称映射
    CONTRACT_NAMES = fetch_contract_names()
    if not CONTRACT_NAMES:
        print("未从数据库加载到合约名称映射，请检查 tianqin_type 表")
        return
    print(f"已从数据库加载 {len(CONTRACT_NAMES)} 个合约名称映射\n")

    api = TqApi(auth=TqAuth(TQ_ACCOUNT, TQ_PASSWORD))

    try:
        print("等待API连接建立...")
        print("API连接已建立\n")

        all_cont_quotes = api.query_cont_quotes()
        prefixes = list(CONTRACT_NAMES.keys())
        cont_quotes = find_cont_quotes_by_prefixs(all_cont_quotes, prefixes)

        if not cont_quotes:
            print("\n未找到任何匹配的合约，请检查前缀是否正确")
            return

        print(f"\n共找到 {len(cont_quotes)} 个匹配的合约")
        print("\n" + "=" * 80)
        print("MACD + KDJ 分析")
        print("=" * 80)

        results = scan_contracts(api, cont_quotes)

        ok_results = [r for r in results if r.get("status") == "完成"]

        print("\n" + "=" * 80)
        print("分析结果汇总")
        print("=" * 80)

        if ok_results:
            print(f"\n共 {len(ok_results)} 个合约分析完成:\n")

            df_data = []
            for r in ok_results:
                df_data.append({
                    "合约代码": r["contract_code"],
                    "合约名称": r.get("contract_name", ""),
                    "当前价格": r.get("price", 0),
                    "趋势方向": r.get("trend", ""),
                    "当前运行": r.get("band", ""),
                    "KDJ信号": r.get("kdj_signal", ""),
                    "KDJ值": r.get("kdj_value", 0),
                    "CCI值": r.get("cci_value", 0),
                    "小时趋势方向": r.get("hour_trend", ""),
                    "小时CCI值": r.get("hour_cci_value", 0),
                })

            df_all = pd.DataFrame(df_data)

            print_aligned_table(
                df_all,
                col_align={"当前价格": "right"},
            )

            # 保存到 MySQL
            save_to_mysql(ok_results)
        else:
            print("\n当前无成功分析的合约")

        errors = sum(1 for r in results if r.get("status") == "错误")
        print(f"\n扫描完成: 共 {len(results)} 个品种，成功 {len(ok_results)} 个，错误 {errors} 个")

    except Exception as e:
        print(f"发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        api.close()
        print("\nAPI连接已关闭")


if __name__ == "__main__":
    main()
