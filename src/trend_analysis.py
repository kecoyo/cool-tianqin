"""
量化策略：判断周期的趋势行情和当前状态（主趋势/浅回调/深回调/浅反弹/深反弹/震荡中）
使用多种技术指标综合判断趋势方向和回调状态
"""
import os
import re
import pandas as pd
import unicodedata
from datetime import datetime
from dotenv import load_dotenv
from tqsdk import TqApi, TqAuth
from tqsdk.ta import MACD
import pymysql

# ── 参数配置 ──────────────────────────────────────────────
# 从 .env 文件加载环境变量（或注册免费账户: https://account.shinnytech.com/）
load_dotenv()
TQ_ACCOUNT = os.getenv("TQ_ACCOUNT", "")
TQ_PASSWORD = os.getenv("TQ_PASSWORD", "")

# MySQL配置
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", 3306)),
    "user": os.getenv("MYSQL_USERNAME", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "cool"),
    "charset": "utf8mb4",
}


def get_mysql_connection():
    """获取 MySQL 数据库连接"""
    return pymysql.connect(**MYSQL_CONFIG)


def fetch_contract_list():
    """从数据库 tianqin_trend 表查询品种列表（status=1 的正常数据）"""
    conn = get_mysql_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, code, name, remark FROM tianqin_trend WHERE status IN (0, 1)"
            )
            rows = cursor.fetchall()
            return [
                {"id": row[0], "code": row[1], "name": row[2], "remark": row[3]}
                for row in rows
            ]
    except Exception as e:
        print(f"从数据库查询品种列表失败: {e}")
        return []
    finally:
        conn.close()


def calculate_ma(klines, periods=[5, 10, 20, 60]):
    """
    计算多条均线
    
    Args:
        klines: K线数据
        periods: 均线周期列表
    
    Returns:
        DataFrame包含各周期均线
    """
    result = {}
    close = klines['close']
    
    for period in periods:
        result[f'MA{period}'] = close.rolling(window=period).mean()
    
    return pd.DataFrame(result)


def calculate_atr(klines, period=14):
    """
    计算ATR（平均真实波幅），用于衡量波动率
    
    Args:
        klines: K线数据
        period: 计算周期
    
    Returns:
        Series: ATR值
    """
    high = klines['high']
    low = klines['low']
    close = klines['close']
    
    # 计算真实波幅
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    return atr


def judge_trend_direction(klines, ma_data, macd_result):
    """
    判断趋势方向：上涨趋势、下跌趋势、震荡

    采用加权评分制，满分100分，分为三个维度：
      - 均线排列   50分  (价格与MA位置关系 + 多周期均线排列，趋势核心确认)
      - 均线斜率   30分  (MA20/MA60斜率方向，衡量趋势是否在发展而非仅看静态排列)
      - MACD方向   20分  (DIF/DEA位置 + DEA在0轴上下，辅助动量确认)

    判定阈值：score >= 55 → 上涨, score <= -55 → 下跌, 否则震荡

    Args:
        klines: K线数据
        ma_data: 均线数据
        macd_result: MACD指标结果

    Returns:
        str: '上涨趋势', '下跌趋势', '震荡'
    """
    current_idx = -1
    close = klines['close'].iloc[current_idx]

    # 获取当前均线值
    ma5 = ma_data['MA5'].iloc[current_idx]
    ma10 = ma_data['MA10'].iloc[current_idx]
    ma20 = ma_data['MA20'].iloc[current_idx]
    ma60 = ma_data['MA60'].iloc[current_idx]

    # 获取MACD值
    dif = macd_result['diff'].iloc[current_idx]
    dea = macd_result['dea'].iloc[current_idx]

    score = 0  # 正数=多头, 负数=空头, 满分±100

    # ── 维度1: 均线排列（50分）──────────────────────────
    # 用 MA 替代 MA+EMA，消除共线冗余，7项差异化权重
    # 长周期排列权重更高，因为它对趋势确认意义更大

    # 价格 vs MA5 (5分) — 最短周期，权重最低
    score += 5 if close > ma5 else -5
    # 价格 vs MA10 (6分) — 短周期
    score += 6 if close > ma10 else -6
    # 价格 vs MA20 (8分) — 中周期，权重较高
    score += 8 if close > ma20 else -8
    # 价格 vs MA60 (8分) — 长周期，突破年线是强趋势信号
    score += 8 if close > ma60 else -8
    # MA5 > MA10 (5分) — 短期排列
    score += 5 if ma5 > ma10 else -5
    # MA10 > MA20 (8分) — 中期排列
    score += 8 if ma10 > ma20 else -8
    # MA20 > MA60 (10分) — 长期排列，权重最高，是趋势的核心确认
    score += 10 if ma20 > ma60 else -10
    # 维度1满分: ±50

    # ── 维度2: 均线斜率方向（30分）──────────────────────
    # 不只看静态排列，还要看均线是否在上行/下行
    # MA20斜率(5根K线) + MA60斜率(10根K线)
    # 阈值0.3%：日线5根K线窗口内0.3%斜率是正常趋势速度

    ma20_prev = ma_data['MA20'].iloc[current_idx - 5]
    ma60_prev = ma_data['MA60'].iloc[current_idx - 10]

    ma20_slope = (ma20 - ma20_prev) / ma20_prev * 100 if ma20_prev != 0 else 0
    ma60_slope = (ma60 - ma60_prev) / ma60_prev * 100 if ma60_prev != 0 else 0

    SLOPE_THRESHOLD = 0.3  # 斜率阈值0.3%

    # MA20斜率: >0.3%为上行(15分), <-0.3%为下行(-15分), 中间按比例
    if ma20_slope > SLOPE_THRESHOLD:
        score += 15
    elif ma20_slope < -SLOPE_THRESHOLD:
        score -= 15
    else:
        score += 15 * (ma20_slope / SLOPE_THRESHOLD)  # 过渡区域线性映射

    # MA60斜率: >0.3%为上行(15分), <-0.3%为下行(-15分)
    if ma60_slope > SLOPE_THRESHOLD:
        score += 15
    elif ma60_slope < -SLOPE_THRESHOLD:
        score -= 15
    else:
        score += 15 * (ma60_slope / SLOPE_THRESHOLD)

    # 维度2满分: ±30

    # ── 维度3: MACD方向（20分）──────────────────────────
    # MACD作为辅助确认指标，权重降低，避免滞后指标拖累趋势初期判定
    # DIF vs DEA (10分) — 金叉/死叉方向
    score += 10 if dif > dea else -10
    # DEA vs 0轴 (10分) — 0轴上方为多头市场，下方为空头市场
    score += 10 if dea > 0 else -10
    # 维度3满分: ±20

    # ── 判定 ────────────────────────────────────────────
    # 阈值55/100 = 55%一致性，要求过半指标同向才判定为趋势
    if score >= 55:
        return '上涨趋势'
    elif score <= -55:
        return '下跌趋势'
    else:
        return '震荡'


def judge_trend_state(klines, ma_data, macd_result, trend_direction, atr):
    """
    判断当前状态：主趋势、浅回调/深回调（含企稳）、浅反弹/深反弹（含企稳）、震荡偏上/偏下/中位

    逻辑：
      上涨趋势:
        - MACD金叉且价格在MA5上方 → 主趋势
        - 否则进入回调判断，以MA20为趋势结构分水岭结合ATR偏离区分深浅:
          · 价格跌破MA20 → 趋势结构受损，深回调
          · 价格在MA20上方但偏离MA5超过1倍ATR → 深回调
          · 其余 → 浅回调
        - 在深/浅回调基础上，判断是否回调企稳（A+C双条件）:
          · 条件A: MACD柱状图连续2根收缩（空头动量衰竭）
          · 条件C: 收盘价反包前2根K线最高价（短期下降结构打破）
          · 同时满足 → 回调企稳
      下跌趋势:
        - MACD死叉且价格在MA5下方 → 主趋势
        - 否则进入反弹判断，以MA20为趋势结构分水岭结合ATR偏离区分深浅:
          · 价格突破MA20 → 趋势结构受损，深反弹
          · 价格在MA20下方但偏离MA5超过1倍ATR → 深反弹
          · 其余 → 浅反弹
        - 在深/浅反弹基础上，判断是否反弹企稳（A+C双条件）:
          · 条件A: MACD柱状图连续2根收缩（多头动量衰竭）
          · 条件C: 收盘价反包前2根K线最低价（短期上升结构打破）
          · 同时满足 → 反弹企稳
      震荡:
        · 价格在MA20上方超过0.5倍ATR → 震荡偏上（接近区间上沿）
        · 价格在MA20下方超过0.5倍ATR → 震荡偏下（接近区间下沿）
        · 其余 → 震荡中位

    Args:
        klines: K线数据
        ma_data: 均线数据
        macd_result: MACD指标结果
        trend_direction: 趋势方向
        atr: ATR值（Series）

    Returns:
        str: '主趋势', '浅回调', '深回调', '浅回调企稳', '深回调企稳',
             '浅反弹', '深反弹', '浅反弹企稳', '深反弹企稳',
             '震荡偏上', '震荡偏下', '震荡中位'
    """
    current_idx = -1
    close = klines['close'].iloc[current_idx]

    ma5 = ma_data['MA5'].iloc[current_idx]
    ma20 = ma_data['MA20'].iloc[current_idx]

    dif = macd_result['diff'].iloc[current_idx]
    dea = macd_result['dea'].iloc[current_idx]
    current_atr = atr.iloc[current_idx]

    # 价格偏离MA5的距离，用ATR标准化
    dev_ma5 = (close - ma5) / current_atr if current_atr != 0 else 0
    # 价格偏离MA20的距离，用ATR标准化（用于震荡位置判断）
    dev_ma20 = (close - ma20) / current_atr if current_atr != 0 else 0

    # ── 回调企稳/反弹企稳判定（A+C双条件）──────────────
    # 条件A: MACD柱状图连续2根收缩（动量衰竭信号）
    #   回调中 bar < 0，bar > prev_bar 说明负柱状图在收缩（空头动量减弱）
    #   反弹中 bar > 0，bar < prev_bar 说明正柱状图在收缩（多头动量减弱）
    #   连续2根收缩过滤单根噪声
    bar_0 = macd_result['bar'].iloc[current_idx]
    bar_1 = macd_result['bar'].iloc[current_idx - 1]
    bar_2 = macd_result['bar'].iloc[current_idx - 2]

    # 条件C: 收盘价反包前2根K线极值（短期结构打破）
    #   回调企稳: close > max(high[-2], high[-3])，即收盘突破前2根高点
    #   反弹企稳: close < min(low[-2], low[-3])，即收盘跌破前2根低点
    high_prev1 = klines['high'].iloc[current_idx - 1]
    high_prev2 = klines['high'].iloc[current_idx - 2]
    low_prev1 = klines['low'].iloc[current_idx - 1]
    low_prev2 = klines['low'].iloc[current_idx - 2]
    prev2_high = max(high_prev1, high_prev2)
    prev2_low = min(low_prev1, low_prev2)

    # 回调企稳: bar连续2根收缩（bar_0 > bar_1 > bar_2）且价格反包前2根高点
    pullback_stabilized = (bar_0 > bar_1 > bar_2) and (close > prev2_high)
    # 反弹企稳: bar连续2根收缩（bar_0 < bar_1 < bar_2）且价格反包前2根低点
    rebound_stabilized = (bar_0 < bar_1 < bar_2) and (close < prev2_low)

    if trend_direction == '上涨趋势':
        # 主趋势确认：价格在MA5上方且MACD金叉
        if close > ma5 and dif > dea:
            return '主趋势'
        # 回调中：价格跌破MA5 或 MACD死叉
        # 深浅回调以MA20为趋势结构分水岭，结合ATR偏离双重判断:
        #   1) 价格跌破MA20 → 趋势结构受损，无论偏离MA5多少都判深回调
        #   2) 价格仍在MA20上方但偏离MA5超过1倍ATR → 回调幅度过大，判深回调
        if close < ma20 or dev_ma5 < -1.0:
            if pullback_stabilized:
                return '深回调企稳'
            return '深回调'
        else:
            if pullback_stabilized:
                return '浅回调企稳'
            return '浅回调'

    elif trend_direction == '下跌趋势':
        # 主趋势确认：价格在MA5下方且MACD死叉
        if close < ma5 and dif < dea:
            return '主趋势'
        # 反弹中：价格突破MA5 或 MACD金叉
        # 深浅反弹以MA20为趋势结构分水岭，结合ATR偏离双重判断:
        #   1) 价格突破MA20 → 趋势结构受损，无论偏离MA5多少都判深反弹
        #   2) 价格仍在MA20下方但偏离MA5超过1倍ATR → 反弹幅度过大，判深反弹
        if close > ma20 or dev_ma5 > 1.0:
            if rebound_stabilized:
                return '深反弹企稳'
            return '深反弹'
        else:
            if rebound_stabilized:
                return '浅反弹企稳'
            return '浅反弹'

    else:
        # 震荡行情：根据价格在MA20±0.5×ATR区间中的位置细分
        if dev_ma20 > 0.5:
            return '震荡偏上'
        elif dev_ma20 < -0.5:
            return '震荡偏下'
        else:
            return '震荡中位'


def calculate_trend_strength(klines, ma_data, atr, macd_result, trend_direction):
    """
    计算趋势强度（0-100），综合均线斜率、价格偏离度、MACD动量、波动一致性四个维度

    趋势行情维度权重:
      - 均线斜率   35分  (MA5/MA20/MA60 斜率方向一致性，衡量趋势速度)
      - 价格偏离度 20分  (价格偏离MA20的程度，衡量趋势扩展程度)
      - MACD动量   30分  (DIF绝对值 + 柱状图扩张方向，衡量趋势动量)
      - 波动一致性 15分  (ATR辅助验证：有趋势方向波动才加分)

    震荡行情维度权重:
      - 波动幅度   50分  (当前ATR相对历史均值的比率，宽幅震荡得分高)
      - MACD收敛度 30分  (柱状图趋零程度，越收敛越符合震荡特征)
      - 均线走平度 20分  (MA20斜率绝对值越小越接近水平，震荡特征越明显)

    Args:
        klines: K线数据
        ma_data: 均线数据
        atr: ATR值（Series）
        macd_result: MACD指标结果
        trend_direction: 趋势方向 ('上涨趋势', '下跌趋势', '震荡')

    Returns:
        float: 趋势强度（0-100），趋势行情衡量趋势强弱，震荡行情衡量震荡活跃度
    """
    current_idx = -1
    close = klines['close'].iloc[current_idx]

    ma5 = ma_data['MA5'].iloc[current_idx]
    ma20 = ma_data['MA20'].iloc[current_idx]
    ma60 = ma_data['MA60'].iloc[current_idx]

    # 当前ATR值（各维度共用）
    current_atr = atr.iloc[current_idx]
    # ATR占价格的百分比，作为斜率/偏离度的自适应满分阈值基准
    # 高波动品种ATR%大 → 满分门槛自动提高，低波动品种同理降低，实现跨品种可比
    atr_pct = (current_atr / close * 100) if close != 0 else 1.0

    # ── 维度1: 均线斜率方向一致性（35分）────────────────
    # 使用5根K线前后的均线变化百分比作为斜率
    ma5_prev = ma_data['MA5'].iloc[current_idx - 5]
    ma20_prev = ma_data['MA20'].iloc[current_idx - 5]

    ma5_slope = (ma5 - ma5_prev) / ma5_prev * 100 if ma5_prev != 0 else 0
    ma20_slope = (ma20 - ma20_prev) / ma20_prev * 100 if ma20_prev != 0 else 0

    # MA60斜率（长周期方向，20根K线窗口）
    ma60_prev = ma_data['MA60'].iloc[current_idx - 20]
    ma60_slope = (ma60 - ma60_prev) / ma60_prev * 100 if ma60_prev != 0 else 0

    if trend_direction == '上涨趋势':
        # 上涨趋势：正向斜率才计分，MA5/MA20/MA60 分别计分
        slope_score = max(0, ma5_slope) + max(0, ma20_slope) + max(0, ma60_slope)
        slope_score /= 3  # 三条均线取平均
    elif trend_direction == '下跌趋势':
        # 下跌趋势：负向斜率才计分
        slope_score = max(0, -ma5_slope) + max(0, -ma20_slope) + max(0, -ma60_slope)
        slope_score /= 3
    else:
        # 震荡行情：均线走平才合理，直接给0分
        slope_score = 0

    # 斜率满分阈值 = 1倍ATR百分比（自适应：高波动品种门槛高，低波动品种门槛低）
    slope_threshold = atr_pct
    slope_score = min(35, slope_score / slope_threshold * 35) if slope_threshold > 0 else 0

    # ── 维度2: 价格偏离度（20分）────────────────────────
    # 只在价格沿趋势方向偏离MA20时才计分，反方向偏离（回调中）不给分
    deviation_ma20 = (close - ma20) / ma20 * 100 if ma20 != 0 else 0
    # 偏离度满分阈值 = 2倍ATR百分比（自适应，与维度1保持同样的ATR标准化逻辑）
    deviation_threshold = atr_pct * 2
    if trend_direction == '上涨趋势':
        # 上涨趋势：价格高于MA20才计分（偏离越大趋势越强）
        deviation_score = min(20, max(0, deviation_ma20) / deviation_threshold * 20) if deviation_threshold > 0 else 0
    elif trend_direction == '下跌趋势':
        # 下跌趋势：价格低于MA20才计分
        deviation_score = min(20, max(0, -deviation_ma20) / deviation_threshold * 20) if deviation_threshold > 0 else 0
    else:
        # 震荡行情：价格偏离MA20不代表趋势强度，不给分
        deviation_score = 0

    # ── 维度3: MACD动量（30分）──────────────────────────
    dif = macd_result['diff'].iloc[current_idx]
    dea = macd_result['dea'].iloc[current_idx]
    bar = macd_result['bar'].iloc[current_idx]
    prev_bar = macd_result['bar'].iloc[current_idx - 1]

    # 动量分两部分：DIF绝对值大小（20分）+ 柱状图扩张方向（10分）
    # Part A: DIF绝对值反映趋势能量，用DIF相对近期均值的比率衡量
    # 取近20根K线的DIF均值作为基准，DIF偏离基准越多说明动量越强
    dif_recent_mean = macd_result['diff'].iloc[-20:].abs().mean()
    dif_ratio = abs(dif) / (dif_recent_mean + 1e-10)
    dif_score = min(20, dif_ratio / 1.5 * 20)  # DIF达到近期均值1.5倍即满分

    # Part B: 柱状图扩张方向是否与趋势方向一致（10分）
    bar_expanding = bar - prev_bar  # 正值=柱状图扩张，负值=柱状图收缩
    if trend_direction == '上涨趋势':
        bar_score = min(10, max(0, bar_expanding) / (abs(bar) + 1e-10) * 10) if bar > 0 else 0
    elif trend_direction == '下跌趋势':
        bar_score = min(10, max(0, -bar_expanding) / (abs(bar) + 1e-10) * 10) if bar < 0 else 0
    else:
        # 震荡行情：柱状图收缩（趋于0）才是即将选择方向，但不计入强度
        bar_score = 0

    macd_score = dif_score + bar_score

    # ── 维度4: 波动一致性（15分）─────────────────────────
    # ATR本身只反映波动率，需要结合趋势方向才有意义：
    # 价格沿趋势方向波动幅度大 → 加分；反方向波动大 → 不加分
    # 用5日净位移除以ATR标准化（以ATR为单位），避免百分比量纲不匹配

    # 近5根K线沿趋势方向的净位移（以ATR为单位）
    recent_close = klines['close'].iloc[current_idx - 5]
    net_move_atr = (close - recent_close) / current_atr if current_atr != 0 else 0

    if trend_direction == '上涨趋势':
        # 上涨趋势中：净涨幅达到2倍ATR即为强一致，映射到0-15分
        move_ratio = max(0, net_move_atr)
        atr_score = min(15, move_ratio / 2.0 * 15)
    elif trend_direction == '下跌趋势':
        move_ratio = max(0, -net_move_atr)
        atr_score = min(15, move_ratio / 2.0 * 15)
    else:
        # 震荡行情：方向不明，不给分
        atr_score = 0

    # ── 综合得分 ────────────────────────────────────────
    if trend_direction == '震荡':
        # 震荡行情：衡量震荡活跃度（宽幅震荡 vs 窄幅震荡），而非趋势强度
        # 维度1: 波动幅度（50分）— 当前ATR相对历史均值的比率
        #   当前ATR高于历史均值 → 宽幅震荡（更活跃），低于 → 窄幅震荡（清淡）
        atr_recent_mean = atr.iloc[-20:].mean()
        atr_ratio = current_atr / atr_recent_mean if atr_recent_mean != 0 else 1.0
        # ATR比率达1.5倍即为宽幅震荡满分，0.5倍以下为窄幅
        volatility_score = min(50, max(0, (atr_ratio - 0.5) / 1.0 * 50))

        # 维度2: MACD收敛度（30分）— 柱状图越趋零越符合震荡特征
        #   |bar| 相对近期均值越小，说明多空力量越均衡（典型震荡）
        bar = macd_result['bar'].iloc[current_idx]
        bar_recent_mean = macd_result['bar'].iloc[-20:].abs().mean()
        bar_ratio = abs(bar) / (bar_recent_mean + 1e-10)
        # bar_ratio=0（完全收敛）→ 满分, bar_ratio=1（正常水平）→ 0分
        convergence_score = min(30, max(0, (1 - bar_ratio) / 1.0 * 30))

        # 维度3: 均线走平度（20分）— MA20斜率绝对值越小越接近水平
        #   斜率越接近0，震荡特征越明显
        ma20_flatness = max(0, 1 - abs(ma20_slope) / atr_pct) if atr_pct > 0 else 0
        flatness_score = min(20, ma20_flatness * 20)

        strength = volatility_score + convergence_score + flatness_score
        strength = max(0, min(100, strength))
        return strength

    strength = slope_score + deviation_score + macd_score + atr_score
    strength = max(0, min(100, strength))

    return strength


def generate_trading_suggestion(trend_direction, trend_state, trend_strength, current_price, ma20, ma5, atr):
    """
    根据趋势分析结果生成波段操作建议

    止损/止盈基于ATR动态计算：
      - 止损固定为 2×ATR
      - 止盈按趋势强度动态调整：强势(≥55) 4×ATR, 中等(35-54) 3×ATR, 温和(<35) 2.5×ATR
    回调/反弹幅度用 ATR 标准化，适应不同品种波动率差异。

    Args:
        trend_direction: 趋势方向 ('上涨趋势', '下跌趋势', '震荡')
        trend_state: 当前状态 ('主趋势', '浅回调', '深回调',
                               '浅反弹', '深反弹',
                               '震荡偏上', '震荡偏下', '震荡中位')
        trend_strength: 趋势强度 (0-100)
        current_price: 当前价格
        ma20: MA20均线值
        ma5: MA5均线值
        atr: ATR值（波动率）

    Returns:
        dict: 包含操作建议的字典
    """
    suggestion = {
        'action': '',           # 操作类型
        'action_detail': '',    # 操作说明（含时机）
        'stop_loss': None,     # 止损位
        'take_profit': None,   # 止盈位
        'reason': ''            # 建议理由
    }

    # 止损固定2倍ATR
    stop_loss_distance = atr * 2
    # 止盈按趋势强度动态调整：强势趋势让利润奔跑
    if trend_strength >= 55:
        take_profit_distance = atr * 4    # 强势趋势，4×ATR
    elif trend_strength >= 35:
        take_profit_distance = atr * 3    # 中等趋势，3×ATR
    else:
        take_profit_distance = atr * 2.5  # 温和趋势，2.5×ATR

    # 回调/反弹幅度：价格偏离MA5的距离，用ATR标准化
    pullback_distance = abs(current_price - ma5) / atr if atr != 0 else 0

    if trend_direction == '上涨趋势':
        if trend_state == '主趋势':
            if trend_strength >= 55:
                # 强势上涨主趋势：持有为主，回调时加仓
                suggestion['action'] = '持有/回调时加仓'
                suggestion['action_detail'] = '趋势强劲，持有为主，回调时加仓'
                suggestion['stop_loss'] = float(current_price - stop_loss_distance)
                suggestion['take_profit'] = float(current_price + take_profit_distance)
                suggestion['reason'] = f'强势上涨主趋势，趋势强度{trend_strength:.1f}%，止损{stop_loss_distance:.1f}点，止盈{take_profit_distance:.1f}点'
            elif trend_strength >= 35:
                # 中等上涨
                suggestion['action'] = '持有'
                suggestion['action_detail'] = '中等强度，持有为主，关注趋势是否加强'
                suggestion['stop_loss'] = float(ma20)  # 以MA20作为止损
                suggestion['take_profit'] = float(current_price + take_profit_distance)
                suggestion['reason'] = f'中等上涨主趋势，趋势强度{trend_strength:.1f}%，以MA20为止损'
            else:
                # 温和上涨
                suggestion['action'] = '持有/观望'
                suggestion['action_detail'] = '趋势较弱，持有观察，等待趋势加强'
                suggestion['stop_loss'] = float(ma20)
                suggestion['take_profit'] = float(current_price + take_profit_distance)
                suggestion['reason'] = f'温和上涨主趋势，趋势强度{trend_strength:.1f}%，趋势偏弱建议观望'

        elif trend_state == '深回调':
            # 深回调：价格已远离MA5，等待回到MA20附近再买入
            suggestion['action'] = '等待买入'
            suggestion['action_detail'] = '深回调中，等待价格回到MA20附近再买入'
            suggestion['stop_loss'] = float(ma20 - stop_loss_distance)  # 止损设在MA20下方2×ATR
            suggestion['take_profit'] = float(ma20 + take_profit_distance)  # 止盈基于MA20买入价
            suggestion['reason'] = f'上涨趋势中的深回调，偏离MA5约{pullback_distance:.1f}倍ATR，等待MA20附近买入，止损MA20下方2×ATR，止盈MA20上方{take_profit_distance:.1f}点'

        elif trend_state == '深回调企稳':
            # 深回调企稳：MACD柱状图连续收缩且价格反包前高，回调可能已结束
            suggestion['action'] = '轻仓买入'
            suggestion['action_detail'] = '深回调企稳，MACD动量衰竭+价格结构转折，可轻仓买入'
            suggestion['stop_loss'] = float(current_price - stop_loss_distance)
            suggestion['take_profit'] = float(current_price + take_profit_distance)
            suggestion['reason'] = f'上涨趋势深回调企稳，偏离MA5约{pullback_distance:.1f}倍ATR，MACD柱状图连续收缩且价格反包前高，回调可能已结束'

        elif trend_state == '浅回调':
            # 浅回调：小幅回调，可轻仓试探
            suggestion['action'] = '轻仓买入/观望'
            suggestion['action_detail'] = '浅回调中，可轻仓试探或等待回调结束'
            suggestion['stop_loss'] = float(current_price - stop_loss_distance)
            suggestion['take_profit'] = float(current_price + take_profit_distance)
            suggestion['reason'] = f'上涨趋势中的浅回调，偏离MA5约{pullback_distance:.1f}倍ATR，建议谨慎操作'

        elif trend_state == '浅回调企稳':
            # 浅回调企稳：MACD柱状图连续收缩且价格反包前高，回调可能已结束
            suggestion['action'] = '买入'
            suggestion['action_detail'] = '浅回调企稳，MACD动量衰竭+价格结构转折，可买入'
            suggestion['stop_loss'] = float(current_price - stop_loss_distance)
            suggestion['take_profit'] = float(current_price + take_profit_distance)
            suggestion['reason'] = f'上涨趋势浅回调企稳，偏离MA5约{pullback_distance:.1f}倍ATR，MACD柱状图连续收缩且价格反包前高，回调可能已结束'

        else:
            # 兜底：trend_state 与 trend_direction 不匹配
            suggestion['action'] = '观望'
            suggestion['action_detail'] = f'状态异常：上涨趋势下出现{trend_state}，观望'
            suggestion['reason'] = f'上涨趋势但状态为{trend_state}，建议观望'

    elif trend_direction == '下跌趋势':
        if trend_state == '主趋势':
            if trend_strength >= 55:
                # 强势下跌主趋势
                suggestion['action'] = '做空/反弹时加空'
                suggestion['action_detail'] = '趋势强劲，做空为主，反弹时加空'
                suggestion['stop_loss'] = float(current_price + stop_loss_distance)
                suggestion['take_profit'] = float(current_price - take_profit_distance)
                suggestion['reason'] = f'强势下跌主趋势，趋势强度{trend_strength:.1f}%，止损{stop_loss_distance:.1f}点，止盈{take_profit_distance:.1f}点'
            elif trend_strength >= 35:
                # 中等下跌
                suggestion['action'] = '做空/减多'
                suggestion['action_detail'] = '中等强度，做空或减多，关注趋势是否加强'
                suggestion['stop_loss'] = float(ma20)
                suggestion['take_profit'] = float(current_price - take_profit_distance)
                suggestion['reason'] = f'中等下跌主趋势，趋势强度{trend_strength:.1f}%，以MA20为止损'
            else:
                # 温和下跌
                suggestion['action'] = '观望/减多'
                suggestion['action_detail'] = '趋势较弱，观望或减多，等待趋势加强'
                suggestion['stop_loss'] = float(ma20)
                suggestion['take_profit'] = float(current_price - take_profit_distance)
                suggestion['reason'] = f'温和下跌主趋势，趋势强度{trend_strength:.1f}%，趋势偏弱建议观望'

        elif trend_state == '深反弹':
            # 深反弹：价格已远离MA5，等待回到MA20附近再做空
            suggestion['action'] = '等待做空'
            suggestion['action_detail'] = '深反弹中，等待价格回到MA20附近再做空'
            suggestion['stop_loss'] = float(ma20 + stop_loss_distance)  # 止损设在MA20上方2×ATR
            suggestion['take_profit'] = float(ma20 - take_profit_distance)  # 止盈基于MA20卖出价
            suggestion['reason'] = f'下跌趋势中的深反弹，偏离MA5约{pullback_distance:.1f}倍ATR，等待MA20附近做空，止损MA20上方2×ATR，止盈MA20下方{take_profit_distance:.1f}点'

        elif trend_state == '深反弹企稳':
            # 深反弹企稳：MACD柱状图连续收缩且价格反包前低，反弹可能已结束
            suggestion['action'] = '轻仓做空'
            suggestion['action_detail'] = '深反弹企稳，MACD动量衰竭+价格结构转折，可轻仓做空'
            suggestion['stop_loss'] = float(current_price + stop_loss_distance)
            suggestion['take_profit'] = float(current_price - take_profit_distance)
            suggestion['reason'] = f'下跌趋势深反弹企稳，偏离MA5约{pullback_distance:.1f}倍ATR，MACD柱状图连续收缩且价格反包前低，反弹可能已结束'

        elif trend_state == '浅反弹':
            # 浅反弹：小幅反弹，可轻仓做空
            suggestion['action'] = '轻仓做空/观望'
            suggestion['action_detail'] = '浅反弹中，可轻仓试探或等待反弹结束'
            suggestion['stop_loss'] = float(current_price + stop_loss_distance)
            suggestion['take_profit'] = float(current_price - take_profit_distance)
            suggestion['reason'] = f'下跌趋势中的浅反弹，偏离MA5约{pullback_distance:.1f}倍ATR，建议谨慎操作'

        elif trend_state == '浅反弹企稳':
            # 浅反弹企稳：MACD柱状图连续收缩且价格反包前低，反弹可能已结束
            suggestion['action'] = '做空'
            suggestion['action_detail'] = '浅反弹企稳，MACD动量衰竭+价格结构转折，可做空'
            suggestion['stop_loss'] = float(current_price + stop_loss_distance)
            suggestion['take_profit'] = float(current_price - take_profit_distance)
            suggestion['reason'] = f'下跌趋势浅反弹企稳，偏离MA5约{pullback_distance:.1f}倍ATR，MACD柱状图连续收缩且价格反包前低，反弹可能已结束'

        else:
            # 兜底：trend_state 与 trend_direction 不匹配
            suggestion['action'] = '观望'
            suggestion['action_detail'] = f'状态异常：下跌趋势下出现{trend_state}，观望'
            suggestion['reason'] = f'下跌趋势但状态为{trend_state}，建议观望'

    else:
        # 震荡行情：区间宽度按震荡强度动态调整
        # 强度高（宽幅震荡）区间更宽，强度低（窄幅震荡）区间更窄
        range_multiplier = 1.0 if trend_strength >= 50 else 0.7
        range_upper = float(ma20 + atr * range_multiplier)
        range_lower = float(ma20 - atr * range_multiplier)

        if trend_state == '震荡偏上':
            # 接近区间上沿，不宜追多，可轻仓试空
            suggestion['action'] = '轻仓做空/观望'
            suggestion['action_detail'] = f'震荡偏上，接近区间上沿{range_upper:.1f}，不宜追多'
            suggestion['stop_loss'] = range_upper
            suggestion['take_profit'] = range_lower
            suggestion['reason'] = f'震荡行情偏上沿，价格偏离MA20上方，接近区间上沿{range_upper:.1f}，可轻仓试空或等待回落'

        elif trend_state == '震荡偏下':
            # 接近区间下沿，不宜追空，可轻仓试多
            suggestion['action'] = '轻仓买入/观望'
            suggestion['action_detail'] = f'震荡偏下，接近区间下沿{range_lower:.1f}，不宜追空'
            suggestion['stop_loss'] = range_lower
            suggestion['take_profit'] = range_upper
            suggestion['reason'] = f'震荡行情偏下沿，价格偏离MA20下方，接近区间下沿{range_lower:.1f}，可轻仓试多或等待反弹'

        else:
            # 震荡中位：方向不明，观望为主
            suggestion['action'] = '观望'
            suggestion['action_detail'] = '震荡中位，等待方向明确'
            suggestion['stop_loss'] = range_lower
            suggestion['take_profit'] = range_upper
            suggestion['reason'] = f'震荡行情中位，方向不明确，建议在[{range_lower:.1f}, {range_upper:.1f}]区间内高抛低吸，等待趋势形成'

    return suggestion


def analyze_trend(api, main_symbol, duration=86400):
    """
    分析单个合约的趋势行情和当前状态
    
    Args:
        api: TqApi实例
        main_symbol: 主力合约代码，如 "GFEX.ps2605"
        duration: K线周期，86400表示日线
    
    Returns:
        dict: 包含分析结果
    """

    try:
        # 获取K线数据
        klines = api.get_kline_serial(main_symbol, duration)
        
        # 获取合约实际名称（如"黄金2512"）
        quote = api.get_quote(main_symbol)
        contract_name = quote.instrument_name if hasattr(quote, 'instrument_name') and quote.instrument_name else ""
        # 主力合约代码（不含交易所代码），如 main_symbol="DCE.p2701" -> "p2701"
        contract_code = main_symbol.split(".", 1)[-1] if "." in main_symbol else main_symbol

        if len(klines) < 100:  # 需要足够的数据计算指标
            return {
                'main_symbol': main_symbol,
                'contract_code': contract_code,
                'contract_name': contract_name,
                'status': '数据不足',
                'reason': f'K线数据不足，当前只有{len(klines)}根，需要至少100根'
            }
        
        # 计算技术指标
        ma_data = calculate_ma(klines, periods=[5, 10, 20, 60])
        macd_result = MACD(klines, 12, 26, 9)
        atr = calculate_atr(klines, period=14)

        # 判断趋势方向
        trend_direction = judge_trend_direction(klines, ma_data, macd_result)
        
        # 判断当前状态
        trend_state = judge_trend_state(klines, ma_data, macd_result, trend_direction, atr)
        
        # 计算趋势强度
        trend_strength = calculate_trend_strength(klines, ma_data, atr, macd_result, trend_direction)
        
        # 获取当前值
        current_idx = -1
        current_price = klines['close'].iloc[current_idx]
        current_ma5 = ma_data['MA5'].iloc[current_idx]
        current_ma20 = ma_data['MA20'].iloc[current_idx]
        current_ma60 = ma_data['MA60'].iloc[current_idx]
        current_dif = macd_result['diff'].iloc[current_idx]
        current_dea = macd_result['dea'].iloc[current_idx]
        current_bar = macd_result['bar'].iloc[current_idx]
        current_atr = atr.iloc[current_idx]
        
        # 生成操作建议
        trading_suggestion = generate_trading_suggestion(
            trend_direction, trend_state, trend_strength,
            current_price, current_ma20, current_ma5, current_atr
        )

        # 构建结果
        result = {
            'main_symbol': main_symbol,
            'contract_code': contract_code,
            'contract_name': contract_name,
            'price': float(current_price),
            'trend_direction': trend_direction,
            'trend_state': trend_state,
            'trend_strength': float(trend_strength),
            'ma5': float(current_ma5),
            'ma20': float(current_ma20),
            'ma60': float(current_ma60),
            'macd_dif': float(current_dif),
            'macd_dea': float(current_dea),
            'macd_bar': float(current_bar),
            'atr': float(current_atr),
            # 操作建议
            'action': trading_suggestion['action'],
            'action_detail': trading_suggestion['action_detail'],
            'stop_loss': trading_suggestion['stop_loss'],
            'take_profit': trading_suggestion['take_profit'],
            'suggestion_reason': trading_suggestion['reason']
        }
        
        return result
        
    except Exception as e:
        return {
            'main_symbol': main_symbol,
            'contract_code': main_symbol.split(".", 1)[-1] if "." in main_symbol else main_symbol,
            'contract_name': '',
            'status': '错误',
            'reason': str(e)
        }


def remove_numbers_from_contract(contract):
    """
    去掉合约代码中的数字，只保留字母部分
    
    Args:
        contract: 合约代码，如 "DCE.b2605" 或 "DCE.bz2603"
    
    Returns:
        str: 去掉数字后的合约代码，如 "DCE.b" 或 "DCE.bz"
    """
    # 去掉合约代码中所有的数字
    return re.sub(r'\d+', '', contract)


def extract_contract_number(contract):
    """
    提取合约代码中的数字部分，用于按到期月份排序

    Args:
        contract: 合约代码，如 "DCE.b2605" 或 "SHFE.cu2602"

    Returns:
        int: 合约数字部分，如 2605；无数字时返回 0
    """
    nums = re.findall(r'\d+', contract)
    return int(nums[-1]) if nums else 0


def find_main_symbols(all_cont_quotes, contract_list):
    """根据品种 code 前缀从 all_cont_quotes 中查找主力合约代码 mainSymbol

    Args:
        all_cont_quotes: api.query_cont_quotes() 返回的全部主力合约列表
        contract_list: 从数据库加载的品种列表，每项含 code/name

    Returns:
        dict: {code: mainSymbol} 映射
    """
    matched = {}

    # 将 all_cont_quotes 统一为合约代码列表
    if isinstance(all_cont_quotes, dict):
        contract_list_all = []
        for key, value in all_cont_quotes.items():
            if isinstance(value, str) and "." in value:
                contract_list_all.append(value)
            elif isinstance(key, str) and "." in key:
                contract_list_all.append(key)
    elif isinstance(all_cont_quotes, list):
        contract_list_all = all_cont_quotes
    else:
        try:
            contract_list_all = list(all_cont_quotes) if hasattr(all_cont_quotes, "__iter__") else []
        except Exception:
            contract_list_all = []

    for item in contract_list:
        code = item["code"]
        name = item["name"]
        prefix_clean = remove_numbers_from_contract(code)
        matches = []
        for contract in contract_list_all:
            if not isinstance(contract, str):
                continue
            contract_clean = remove_numbers_from_contract(contract)
            if contract_clean == prefix_clean:
                matches.append(contract)

        if matches:
            # 找到多个匹配时，按合约代码中的数字部分排序，取最大的（最新到期月份）
            main_symbol = sorted(matches, key=extract_contract_number, reverse=True)[0]
            matched[code] = main_symbol
            print(f"  ✅ {code} ({name}) -> {main_symbol}")
        else:
            print(f"  ⚠ 警告: 未找到匹配 {code} ({name}) 的合约")

    return matched


def save_to_mysql(results):
    """将分析结果更新到 MySQL tianqin_trend 表（按 code 更新）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:00")
    conn = get_mysql_connection()
    try:
        with conn.cursor() as cursor:
            sql = """
                UPDATE tianqin_trend SET
                    updateTime = %s,
                    mainSymbol = %s,
                    contractCode = %s,
                    contractName = %s,
                    price = %s,
                    trendDirection = %s,
                    trendState = %s,
                    trendStrength = %s,
                    action = %s,
                    actionDetail = %s
                WHERE code = %s
            """
            values = []
            for r in results:
                values.append((
                    now,
                    r.get("main_symbol", ""),
                    r.get("contract_code", ""),
                    r.get("contract_name", ""),
                    round(float(r.get("price", 0)), 2),
                    r.get("trend_direction", ""),
                    r.get("trend_state", ""),
                    round(float(r.get("trend_strength", 0)), 1),
                    r.get("action", ""),
                    r.get("action_detail", ""),
                    r.get("code", ""),
                ))
            cursor.executemany(sql, values)
        conn.commit()
        print(f"  MySQL 更新成功: {len(results)} 条记录 -> tianqin_trend")
    except Exception as e:
        conn.rollback()
        print(f"  MySQL 更新失败: {e}")
    finally:
        conn.close()


def scan_contracts(api, contract_list, main_symbol_map, duration=86400):
    """扫描多个合约，分析趋势行情

    Args:
        api: TqApi实例
        contract_list: 从数据库加载的品种列表
        main_symbol_map: {code: mainSymbol} 映射
        duration: K线周期，86400表示日线

    Returns:
        list: 分析结果列表
    """
    results = []

    for item in contract_list:
        code = item["code"]
        name = item["name"]
        main_symbol = main_symbol_map.get(code)
        if not main_symbol:
            continue

        print(f"正在分析: {main_symbol} ({name})")
        result = analyze_trend(api, main_symbol, duration)
        result["code"] = code
        result["name"] = name
        results.append(result)

        # 打印结果
        if "trend_direction" in result:
            print(f"  ✅ {result['trend_direction']} | {result['trend_state']} | 强度: {result['trend_strength']:.1f}%")
            if "action" in result:
                print(f"    💡 操作建议: {result['action']} | {result['action_detail']}")
        elif result.get("status") == "错误":
            print(f"  ✗ 错误: {result.get('reason', '未知错误')}")
        else:
            print(f"  - {result.get('status', '未知状态')}")

    return results


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


def main():
    """主函数"""
    # 从数据库加载品种列表
    contract_list = fetch_contract_list()
    if not contract_list:
        print("未从数据库加载到品种列表，请检查 tianqin_trend 表")
        return
    print(f"已从数据库加载 {len(contract_list)} 个品种\n")

    # 初始化API
    api = TqApi(auth=TqAuth(TQ_ACCOUNT, TQ_PASSWORD))

    try:
        print("等待API连接建立...")
        print("API连接已建立\n")

        # 1. 获取全部主力合约列表
        all_cont_quotes = api.query_cont_quotes()

        # 2. 根据品种 code 前缀查找主力合约代码 mainSymbol
        print("=" * 80)
        print("根据品种代码查找匹配的主力合约")
        print("=" * 80)

        main_symbol_map = find_main_symbols(all_cont_quotes, contract_list)

        if not main_symbol_map:
            print("\n未找到任何匹配的合约，请检查品种代码是否正确")
            return

        print(f"\n共找到 {len(main_symbol_map)} 个匹配的合约")
        print("\n" + "=" * 80)
        print("趋势行情分析")
        print("=" * 80)

        # 3. 遍历品种列表，通过 mainSymbol 分析数据
        results = scan_contracts(api, contract_list, main_symbol_map, duration=86400)

        # 筛选有效结果
        valid_results = [r for r in results if "trend_direction" in r]

        print("\n" + "=" * 80)
        print("分析结果汇总")
        print("=" * 80)

        if valid_results:
            print(f"\n共分析 {len(valid_results)} 个合约:\n")

            df_data = []
            for r in valid_results:
                df_data.append({
                    "品种代码": r["code"],
                    "品种名称": r.get("name", ""),
                    "主力合约": r.get("main_symbol", ""),
                    "合约代码": r.get("contract_code", ""),
                    "合约名称": r.get("contract_name", ""),
                    "当前价格": r.get("price", 0),
                    "趋势方向": r.get("trend_direction", ""),
                    "当前状态": r.get("trend_state", ""),
                    "趋势强度": r.get("trend_strength", 0),
                    "操作建议": r.get("action", ""),
                    "操作详情": r.get("action_detail", ""),
                })

            df_all = pd.DataFrame(df_data)

            print_aligned_table(
                df_all,
                col_align={"当前价格": "right", "趋势强度": "right"},
            )

            # 4. 保存到 MySQL（UPDATE 更新）
            save_to_mysql(valid_results)

            # 按趋势方向分类统计
            print("\n" + "=" * 80)
            print("趋势方向统计")
            print("=" * 80)
            trend_stats = {}
            for r in valid_results:
                direction = r.get("trend_direction", "未知")
                state = r.get("trend_state", "未知")
                key = f"{direction}-{state}"
                trend_stats[key] = trend_stats.get(key, 0) + 1

            for key, count in sorted(trend_stats.items()):
                print(f"  {key}: {count} 个")
        else:
            print("\n未找到有效分析结果")

        # 显示详细结果
        print("\n" + "=" * 80)
        print("详细分析结果")
        print("=" * 80)
        for r in results:
            if "trend_direction" in r:
                name = r.get("name", r["main_symbol"])
                print(f"\n{r['main_symbol']} ({name}):")
                print(f"  当前价格: {r['price']:.2f}")
                print(f"  趋势方向: {r['trend_direction']}")
                print(f"  当前状态: {r['trend_state']}")
                print(f"  趋势强度: {r['trend_strength']:.1f}%")
                print(f"  ATR: {r['atr']:.2f}")
                if "action" in r:
                    print(f"\n  💡 波段操作建议:")
                    print(f"    操作类型: {r['action']}")
                    print(f"    操作详情: {r['action_detail']}")
                    if r.get("suggestion_reason"):
                        print(f"    建议理由: {r['suggestion_reason']}")
            elif r.get("status"):
                print(f"\n{r['main_symbol']}: {r.get('status')} - {r.get('reason', '')}")

    except Exception as e:
        print(f"发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        api.close()
        print("\nAPI连接已关闭")


if __name__ == "__main__":
    main()
