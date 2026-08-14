import asyncio
import time
import logging

# Suppress verbose debug logs during benchmark
logging.getLogger("app.bqca.client").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from app.bqca.client import _get_credentials, create_conversation, chat_stream_events, BQCAEventType

TEST_QUESTIONS = [
    {
        "category": "1. 简单数据查询",
        "domain": "ecommerce",
        "question": "查询 2025 年销售额 Top 5 的国家",
    },
    {
        "category": "2. 复杂多表/多条件",
        "domain": "game",
        "question": "按设备系统统计 2025-09-04 至 2025-10-03 的活跃用户数、新增用户数和次日留存率，比较 Android 和 iOS 的用户表现差异。",
    },
    {
        "category": "3. 深度分析归纳",
        "domain": "game",
        "question": "对比 Android 和 iOS 用户在活跃、留存、游戏时长和关卡失败率上的表现，判断是否存在某个设备系统体验明显偏弱的问题。",
    },
    {
        "category": "4. 趋势预测与建议",
        "domain": "ecommerce",
        "question": "根据近半年的销售额与毛利率波动趋势，预测未来一个季度的热门商品品类，并提出备货建议。",
    },
]


async def run_benchmark():
    print("\n" + "=" * 95)
    print("🚀 BQCA 响应耗时基准测试 (带 Session 预先建立拆分)")
    print("=" * 95)

    results = []

    for item in TEST_QUESTIONS:
        category = item["category"]
        question = item["question"]
        domain = item["domain"]

        agent_id = "game-analyst-cn" if domain == "game" else "ecommerce-analyst-cn"
        location = "global"

        print(f"\n▶ 正在测试 [{category}]")
        print(f"  问题: \"{question}\"")

        # Step A: Measure Session Creation Latency
        t_session_start = time.time()
        creds = _get_credentials()
        convo_name = await asyncio.to_thread(create_conversation, creds, agent_id=agent_id, location=location)
        t_session = time.time() - t_session_start
        print(f"  🔑 0. 云端 Session 建立耗时: {t_session:.2f}s (Convo: {convo_name[-20:]})")

        # Step B: Measure Stream Chat Latency
        start_time = time.time()

        t_first_event = None
        t_sql = None
        t_data = None
        t_final = None

        has_sql = False
        row_count = 0
        summary_len = 0

        try:
            async for event in chat_stream_events(
                question,
                conversation_name=convo_name,
                agent_id=agent_id,
                location=location,
            ):
                now = time.time() - start_time
                if t_first_event is None:
                    t_first_event = now

                if event.event_type == BQCAEventType.SQL and t_sql is None:
                    t_sql = now
                    has_sql = True
                elif event.event_type == BQCAEventType.DATA and t_data is None:
                    t_data = now
                    if isinstance(event.data, list):
                        row_count = len(event.data)
                elif event.event_type == BQCAEventType.FINAL:
                    t_final = now
                    if event.data:
                        summary_len = len(str(event.data))

        except Exception as e:
            print(f"  ❌ 测试出错: {e}")

        print(f"  ⏱️  1. 真正首包耗时 (TTFT):   {t_first_event:.2f}s" if t_first_event else "  ⏱️  1. 首包耗时: N/A")
        print(f"  ⚡ 2. SQL 生成耗时:       {t_sql:.2f}s" if t_sql else "  ⚡ 2. SQL 生成耗时:       未生成 SQL")
        print(f"  📊 3. 数据查库耗时:       {t_data:.2f}s (获取 {row_count} 行数据)" if t_data else "  📊 3. 数据查库耗时:       无新数据查库")
        print(f"  🎯 4. 最终洞察耗时:       {t_final:.2f}s (输出文本 {summary_len} 字)" if t_final else "  🎯 4. 最终洞察耗时:       N/A")

        results.append({
            "category": category,
            "question": question,
            "session_time": t_session,
            "ttft": t_first_event or 0.0,
            "sql_time": t_sql or 0.0,
            "data_time": t_data or 0.0,
            "total_time": t_final or 0.0,
            "has_sql": has_sql,
            "rows": row_count,
            "length": summary_len,
        })

        await asyncio.sleep(2)

    print("\n" + "=" * 95)
    print("📊 BQCA 各类问题响应耗时精细化排查报告 (Fine-grained Latency Benchmark):")
    print("=" * 95)
    header = f"| {'测试问题类别':<16} | {'Session建连':<10} | {'流式首包TTFT':<10} | {'SQL生成时刻':<10} | {'查库完成时刻':<10} | {'最终对话总耗时':<12} | {'SQL/数据行':<10} |"
    divider = "| " + " | ".join(["-" * 16, "-" * 10, "-" * 10, "-" * 10, "-" * 10, "-" * 12, "-" * 10]) + " |"
    print(header)
    print(divider)

    for r in results:
        sql_str = f"有 ({r['rows']}行)" if r["has_sql"] else "无 (纯分析)"
        sql_t_str = f"{r['sql_time']:.2f}s" if r["has_sql"] else "-"
        data_t_str = f"{r['data_time']:.2f}s" if r["has_sql"] else "-"
        row = f"| {r['category']:<16} | {r['session_time']:>8.2f}s   | {r['ttft']:>8.2f}s   | {sql_t_str:>8}   | {data_t_str:>8}   | {r['total_time']:>10.2f}s   | {sql_str:<10} |"
        print(row)

    print("=" * 95 + "\n")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
